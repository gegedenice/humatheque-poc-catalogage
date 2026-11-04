"""Workflow de bout en bout avec services simulés (SPEC §6, §11 étape 5)."""

import copy
import json
from io import BytesIO

import httpx
import pytest
import respx
from PIL import Image

import services_simules as sim
from services_simules import EXTRACTION, VLM, png, simuler
from humatheque.extraction.fournisseurs import FournisseurOpenAICompatible
from humatheque.services import ClientService
from humatheque.spec import charger_spec
from humatheque.workflow import (
    Document, Dossier, ResolutionImpossible, Services, Workflow, choisir_provenance, declarer_faux_positif,
)

@pytest.fixture(scope="module")
def spec():
    return charger_spec(source="csv")


@pytest.fixture
def services(spec):
    return sim.services(spec)


@pytest.fixture
def workflow(services):
    return Workflow(services, relances=2, delai=0, attendre=lambda s: None)


IMAGES: dict[str, str] = {}


@pytest.fixture(autouse=True, scope="module")
def _images(tmp_path_factory):
    dossier_img = tmp_path_factory.mktemp("images")
    (dossier_img / "001.png").write_bytes(png())
    (dossier_img / "illisible.png").write_bytes(b"pas une image")
    IMAGES.update(page=str(dossier_img / "001.png"), illisible=str(dossier_img / "illisible.png"))


def dossier(type_document="these", images=None):
    return Dossier(id="d1", document=Document(id="TH/TH-01", type_document=type_document,
                                              images=images if images is not None else [IMAGES["page"]]))


# ================================================================ scénarios du §11
@respx.mock
def test_s08_produit_une_notice(workflow):
    routes = simuler()
    d = workflow.executer(dossier())
    assert d.decision.situation == "S08" and d.decision.profil_cible == "these_originale_imprimee"
    assert d.routage == "auto" and d.etape_arret is None
    assert all(e.statut in ("ok", "ignoree") for e in d.etapes.values())
    lignes = d.notice_texte.splitlines()
    assert "200 1#$aContribution à l'étude de la démocratie en Afrique$fJean-Daniel DUBOIS" \
           "$gsous la direction de Pr Marie-France de LA ROCHE" not in lignes  # civilité retirée de la forme
    assert "700 #1$3026940345$4070" in lignes
    assert "701 #1$3026940345$4958$4555" in lignes
    assert "711 02$3026375478$4295" in lignes
    assert "686 ##$a900$2TEF" in lignes
    assert "029 ##$aFR$b1998EPHEXXXX" in lignes
    assert routes["idref"].call_count == 3  # auteur, directeur, rapporteur
    # ordre imposé : Alma avant Sudoc (ALM-SUD-F-001), route selon le type (ING-T-005)
    assert routes["alma"].calls[0].request.url.path == "/check/thesis"
    urls = [c.request.url.host for c in respx.calls]
    assert urls.index("alma.test") < urls.index("sudoc.test")
    # traçabilité
    types = {e.type for e in d.journal}
    assert {"appel", "transformation", "decision", "zone"} <= types
    assert d.extraction.bruts and d.extraction.prompt.empreinte  # VLM-T-004, VLM-T-005
    assert d.version_spec and "numero_nnt:bouchon" in d.alertes


@respx.mock
def test_s08_sans_civilite_dans_200g(workflow):
    simuler()
    d = workflow.executer(dossier())
    (z200,) = d.notice.zones_de("200")
    assert z200.texte().endswith("$gsous la direction de Marie-France de LA ROCHE")


@respx.mock
@pytest.mark.parametrize("statut", ["academic_record_found", "ambiguous_academic_candidate", "off_profile_match_only"])
def test_s01_arret_avec_ppn_alma(workflow, statut):
    routes = simuler(alma=sim.alma(statut, ppn="229969437"))
    d = workflow.executer(dossier())
    assert d.decision.situation == "S01"
    assert (d.routage, d.etape_arret) == ("sortie", "situation")
    assert d.alma.ppn == "229969437" and "229969437" in d.motif_arret
    assert not routes["sudoc"].called and d.etapes["sudoc"].statut == "ignoree"
    assert not routes["idref"].called and d.notice is None


@respx.mock
def test_s06_conversion_copie_puis_arret(workflow):
    routes = simuler(sudoc=sim.sudoc([("229969437", 0.95, "electronic")], "electronic_only"))
    d = workflow.executer(dossier())
    assert d.decision.situation == "S06" and d.decision.suite == "convertir"
    assert (d.routage, d.etape_arret) == ("conversion", "conversion")
    assert d.ppn_source == "229969437"
    assert d.notice_convertie.iso == d.notice_source_iso  # copie de la source (v1)
    assert d.notice_source_iso.startswith("0") and "\x1d" in d.notice_source_iso  # ISO 2709
    assert d.notice_convertie.texte == d.notice_source
    assert "029 ##$aFR$b2017EHES0033" in d.notice_source.splitlines()
    assert "conversion_non_implementee" in d.alertes
    assert d.notice_convertie.profil_cible == "these_originale_imprimee"
    assert "https://www.sudoc.fr/229969437" in d.motif_arret
    assert not routes["idref"].called and d.notice is None
    # Relance : pas de nouvelle conversion ni d'IdRef
    workflow.executer(d)
    assert routes["notice"].call_count == 1 and not routes["idref"].called and d.routage == "conversion"


@respx.mock
def test_provenance_ambigue_arret_puis_reprise(workflow):
    routes = simuler(extraction=EXTRACTION | {"granting_institution": "École des hautes études"})
    d = workflow.executer(dossier())
    assert (d.routage, d.etape_arret) == ("décision", "provenance")
    assert "provenance à confirmer" in d.motif_arret
    assert not routes["alma"].called
    assert d.exceptions[0].etape == "provenance" and not d.exceptions[0].resolue

    choisir_provenance(d, "EPHE", auteur="catalogueur", commentaire="EPHE, section IV")
    workflow.executer(d)
    assert d.provenance.valeur == "EPHE" and d.provenance.regle == "choix_catalogueur"
    assert d.decision.situation == "S08" and d.notice is not None and d.routage == "auto"
    assert routes["vlm"].call_count == 1  # extraction non rappelée (ING-F-007)
    assert any(e.type == "resolution" and e.auteur == "catalogueur" for e in d.journal)


def test_provenance_choix_invalide():
    with pytest.raises(ResolutionImpossible):
        choisir_provenance(dossier(), "peut-être", auteur="x")


# ================================================================ reprises et erreurs
@respx.mock
def test_faux_positif_alma_reprise(workflow):  # ALM-SUD-006
    routes = simuler(alma=sim.alma("off_profile_match_only", ppn="111"))
    d = workflow.executer(dossier())
    assert d.routage == "sortie"
    with pytest.raises(ResolutionImpossible):
        declarer_faux_positif(d, "alma", "", auteur="cat")
    declarer_faux_positif(d, "alma", "notice d'un autre ouvrage", auteur="cat")
    workflow.executer(d)
    assert d.etat_alma == "absent" and d.decision.situation == "S08" and d.notice is not None
    assert routes["alma"].call_count == 1 and routes["sudoc"].call_count == 1
    assert any(e.type == "resolution" and e.detail["avant"] == "présent" for e in d.journal)


@respx.mock
def test_sudoc_indetermine_puis_faux_positif(workflow):
    routes = simuler(sudoc=sim.sudoc([("1", 0.9, "printed_reproduction"), ("2", 0.8, "unknown")], "duplicate_found"))
    d = workflow.executer(dossier())
    assert (d.routage, d.etape_arret, d.etat_sudoc) == ("décision", "sudoc", "indéterminé")
    assert d.motif_arret == "nature de la notice Sudoc non qualifiable"
    declarer_faux_positif(d, "sudoc", "candidats sans rapport", auteur="cat")
    workflow.executer(d)
    assert d.etat_sudoc == "rien" and d.decision.situation == "S08"
    assert routes["sudoc"].call_count == 1


@respx.mock
def test_thesis_state_absent_incident(workflow):
    simuler(sudoc=sim.sudoc([("1", 0.9, None)], "duplicate_found"))
    d = workflow.executer(dossier())
    assert (d.routage, d.etape_arret) == ("incident", "sudoc")
    assert "thesis_state absent" in d.motif_arret


@respx.mock
def test_relance_automatique_puis_succes(workflow):
    routes = simuler()
    routes["alma"].side_effect = [httpx.Response(503), httpx.Response(200, json=sim.alma())]
    d = workflow.executer(dossier())
    assert d.routage == "auto" and d.etapes["alma"].tentatives == 2
    assert any(e.type == "relance" for e in d.journal)


@respx.mock
def test_service_indisponible_puis_relance_manuelle(workflow):  # ING-F-007
    routes = simuler()
    routes["sudoc"].side_effect = httpx.ConnectError("injoignable")
    d = workflow.executer(dossier())
    assert (d.routage, d.etape_arret) == ("incident", "sudoc")
    assert d.etapes["sudoc"].statut == "service_indisponible" and d.etapes["sudoc"].tentatives == 3
    assert d.etapes["sudoc"].erreur

    routes["sudoc"].side_effect = None
    routes["sudoc"].return_value = httpx.Response(200, json=sim.sudoc([]))
    workflow.executer(d)
    assert d.routage == "auto" and d.notice is not None
    assert routes["vlm"].call_count == 1 and routes["alma"].call_count == 1


@respx.mock
def test_relance_explicite_depuis_une_etape(workflow):
    routes = simuler()
    d = workflow.executer(dossier())
    workflow.executer(d, depuis="extraction")
    assert routes["vlm"].call_count == 2


@respx.mock
def test_json_invalide_incident(workflow):
    respx.post(VLM).respond(json={"choices": [{"message": {"content": "pas de JSON"}}]})
    d = workflow.executer(dossier())
    assert (d.routage, d.etapes["extraction"].statut) == ("incident", "json_invalide")
    assert d.etapes["extraction"].reponse == {"bruts": ["pas de JSON"]}


@respx.mock
@pytest.mark.parametrize("panne, statut", [(httpx.ReadTimeout("lent"), "inference_timeout"),
                                           (httpx.Response(503), "inference_indisponible")])
def test_inference_incident(workflow, panne, statut):
    route = respx.post(VLM)
    if isinstance(panne, Exception):
        route.side_effect = panne
    else:
        route.return_value = panne
    d = workflow.executer(dossier())
    assert (d.routage, d.etapes["extraction"].statut) == ("incident", statut)
    assert route.call_count == 3


@respx.mock
def test_image_inaccessible_incident(workflow):
    respx.get("https://minio.test/TH/TH-01/001.jpg").respond(404)
    d = workflow.executer(dossier(images=["https://minio.test/TH/TH-01/001.jpg"]))
    assert (d.routage, d.etapes["extraction"].statut) == ("incident", "image_inaccessible")


@pytest.mark.parametrize("images", [[], ["illisible"]])
def test_document_invalide(workflow, images):  # ING-F-008
    d = workflow.executer(dossier(images=[IMAGES[i] for i in images]))
    assert (d.routage, d.etape_arret) == ("décision", "extraction")
    assert any(a.startswith("document_invalide") for a in d.alertes)


@respx.mock
def test_document_sans_metadonnee(workflow):
    simuler(extraction={"title": None, "advisor": [], "confidence": 0.1})
    d = workflow.executer(dossier())
    assert (d.routage, d.etape_arret) == ("décision", "extraction")
    assert "document_invalide:aucune métadonnée extraite" in d.alertes


@respx.mock
def test_idref_decision_non_bloquante_avec_repli(workflow):
    simuler(idref_statut="low_confidence")
    d = workflow.executer(dossier())
    assert d.notice is not None and d.routage == "décision"
    assert "700 #1$aDubois$bJean-Daniel$4070" in d.notice_texte.splitlines()
    assert {e.etape for e in d.exceptions} == {"idref_personnes"} and len(d.exceptions) == 3
    assert d.exceptions[0].candidats[0]["ppn"] == "026940345"


@respx.mock
def test_idref_not_found_proto_notice(workflow):
    simuler(idref_statut="not_found")
    d = workflow.executer(dossier())
    assert any("proto-notice d'autorité à créer" in a for a in d.alertes)


@respx.mock
def test_organisations_low_confidence_exception(workflow):
    simuler(orgs=sim.idref_organisations(), extraction=EXTRACTION | {"co_tutelle_institutions": ["Université de Genève"]})
    d = workflow.executer(dossier())
    assert any(e.etape == "idref_organisations" and "co_tutelle" in e.objet for e in d.exceptions)
    assert "711 02$aUniversité de Genève$4995" in d.notice_texte.splitlines()


@respx.mock
def test_memoire_route_et_provenance(workflow):
    routes = simuler(extraction=EXTRACTION | {"degree_type": "Mémoire de master recherche 2e année"})
    d = workflow.executer(dossier(type_document="memoire"))
    assert routes["alma"].calls[0].request.url.path == "/check/dissertation"
    assert routes["sudoc"].calls[0].request.url.path == "/check/dissertation"
    assert d.provenance.valeur == "mémoire" and d.decision.situation == "S12"
    assert "701 #1$3026940345$4003" in d.notice_texte.splitlines()


@respx.mock
def test_generation_echec(workflow, spec_fabriquee):
    spec_fabriquee.modifier("Regles", "id", "R005", gabarit="101 0#$a")
    workflow.services.spec = charger_spec(source="csv", dossier=spec_fabriquee.dossier)
    simuler()
    d = workflow.executer(dossier())
    assert (d.routage, d.etapes["generation"].statut) == ("incident", "generation_echec")


@respx.mock
def test_dossier_serialisable(workflow):
    simuler()
    d = workflow.executer(dossier())
    copie = Dossier.model_validate_json(d.model_dump_json())
    assert copie.notice_texte == d.notice_texte and copie.schema_version == 1


@respx.mock
def test_relance_automatique_sans_exceptions_en_double(workflow):
    routes = simuler(idref_statut="low_confidence")
    routes["idref"].side_effect = [httpx.Response(200, json=sim.idref_personne("low_confidence")),
                                   httpx.ReadTimeout("lent"),
                                   *[httpx.Response(200, json=sim.idref_personne("low_confidence"))] * 3]
    d = workflow.executer(dossier())
    assert d.etapes["idref_personnes"].tentatives == 2
    assert len([e for e in d.exceptions if e.etape == "idref_personnes"]) == 3
