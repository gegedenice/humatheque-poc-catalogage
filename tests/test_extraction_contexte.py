"""Extraction VLM (fournisseur simulé par respx) et construction du contexte (SPEC §5)."""

import json
from io import BytesIO

import httpx
import pytest
import respx
from PIL import Image

from humatheque.contexte import ResultatsServices, construire, document_vide, normaliser_metadonnees
from humatheque.extraction import ExtractionInvalide, charger_prompt, consolider, extraire
from humatheque.extraction.fournisseurs import FournisseurOpenAICompatible, InferenceIndisponible
from humatheque.extraction.images import ImageInaccessible
from humatheque.services.idref import AlignementIdref, AlignementOrganisations
from humatheque.spec import charger_spec

VLM = "https://vlm.test/v1/chat/completions"


def png() -> bytes:
    buf = BytesIO()
    Image.new("RGB", (2000, 1000), "white").save(buf, format="PNG")
    return buf.getvalue()


def reponse_vlm(contenu) -> dict:
    texte = contenu if isinstance(contenu, str) else json.dumps(contenu, ensure_ascii=False)
    return {"choices": [{"message": {"content": texte}}]}


EXTRACTION = {
    "title": "CONTRIBUTION A L'ETUDE DE LA DEMOCRATIE EN AFRIQUE",
    "subtitle": "LE CAS DU SENEGAL",
    "author": "Jean-Daniel DUBOIS",
    "degree_type": "Thèse de doctorat",
    "discipline": "SCIENCE POLITIQUE",
    "volume": None,
    "granting_institution": "ECOLE PRATIQUE DES HAUTES ETUDES",
    "co_tutelle_institutions": [],
    "doctoral_school": None,
    "defense_year": 1998,
    "advisor": ["Pr Marie-France de LA ROCHE"],
    "jury_president": None,
    "reviewers": "Luc PETIT | Claire DURAND",
    "committee_members": [],
    "language": "fre",
    "abstract": None,
    "confidence": 0.9,
}


def fournisseur():
    return FournisseurOpenAICompatible("vlm", VLM, "google/gemma-4-26B-A4B-it", timeout=5)


# --- Extraction
def test_prompt_externalise():  # VLM-T-005
    p = charger_prompt("these")
    assert p.nom == "extraction_these.txt" and len(p.empreinte) == 12
    assert "Transcribe every value exactly as printed" in p.texte
    assert "sentence case" not in p.texte and "civility" not in p.texte.replace("civilities", "")
    assert '"abstract"' in p.texte  # VLM-T-002
    assert "Mémoire de DEA" in charger_prompt("memoire").texte


@respx.mock
def test_requete_unique_toutes_les_images():  # VLM-T-001, VLM-T-004
    r = respx.post(VLM).respond(json=reponse_vlm("```json\n" + json.dumps(EXTRACTION) + "\n```"))
    res = extraire(fournisseur(), [png(), png()], "these", "requete_unique")
    corps = json.loads(r.calls[0].request.content)
    images = [c for c in corps["messages"][0]["content"] if c["type"] == "image_url"]
    assert len(r.calls) == 1 and len(images) == 2
    assert images[0]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert res.bruts[0].startswith("```json")  # sortie brute conservée telle quelle
    assert res.extraction["title"] == EXTRACTION["title"]  # pas de normalisation
    assert res.appels[0].requete["messages"][0]["content"][1] == {"type": "images", "nombre": 2}


@respx.mock
def test_par_image_consolidation_et_divergence():
    respx.post(VLM).mock(side_effect=[
        httpx.Response(200, json=reponse_vlm({"title": "TITRE", "abstract": None, "defense_year": "1998"})),
        httpx.Response(200, json=reponse_vlm({"title": "AUTRE", "abstract": "Résumé", "defense_year": None})),
    ])
    res = extraire(fournisseur(), [png(), png()], "these", "par_image")
    assert res.extraction == {"title": "TITRE", "abstract": "Résumé", "defense_year": "1998"}
    assert res.alertes == ["extraction_divergente:title"]
    assert len(res.bruts) == 2


def test_consolider_listes():
    r, a = consolider([{"advisor": []}, {"advisor": ["A"]}])
    assert r == {"advisor": ["A"]} and a == []


@respx.mock
def test_json_invalide():
    respx.post(VLM).respond(json=reponse_vlm("Je ne peux pas lire cette page."))
    with pytest.raises(ExtractionInvalide) as e:
        extraire(fournisseur(), [png()], "these")
    assert e.value.bruts == ["Je ne peux pas lire cette page."]


@respx.mock
@pytest.mark.parametrize("panne, timeout", [(httpx.ReadTimeout("lent"), True), (httpx.Response(503), False)])
def test_inference_indisponible(panne, timeout):
    route = respx.post(VLM)
    if isinstance(panne, Exception):
        route.side_effect = panne
    else:
        route.return_value = panne
    with pytest.raises(InferenceIndisponible) as e:
        extraire(fournisseur(), [png()], "these")
    assert e.value.timeout is timeout


@respx.mock
def test_image_inaccessible():
    respx.get("https://minio.test/TH/TH-01/001.jpg").respond(404)
    with pytest.raises(ImageInaccessible):
        extraire(fournisseur(), ["https://minio.test/TH/TH-01/001.jpg"], "these")
    with pytest.raises(ImageInaccessible):
        extraire(fournisseur(), [b"pas une image"], "these")
    with pytest.raises(ImageInaccessible):
        extraire(fournisseur(), [], "these")


# --- Contexte
def test_normaliser_metadonnees():
    m = normaliser_metadonnees(EXTRACTION)
    assert m["reviewers"] == ["Luc PETIT", "Claire DURAND"]
    assert m["defense_year"] == "1998" and m["abstract"] is None
    assert not document_vide(m)
    assert document_vide(normaliser_metadonnees({"confidence": 0.1}))


def test_contexte_cles_conformes_a_sources():
    ctx = construire(normaliser_metadonnees(EXTRACTION), "these")
    ids = {s.id for s in charger_spec(source="csv").sources}
    assert set(ctx.valeurs) <= ids


def test_contexte_valeurs():
    m = normaliser_metadonnees(EXTRACTION)
    res = ResultatsServices(
        personnes={"author": [AlignementIdref(statut="accepted", ppn="TEST00001")],
                   "reviewers": [AlignementIdref(statut="low_confidence"), AlignementIdref(statut="accepted", ppn="P2")]},
        organisations=AlignementOrganisations(granting_institution=AlignementIdref(statut="accepted", ppn="026375478")),
        dewey_code="320",
    )
    c = construire(m, "these", res).valeurs
    assert c["title"] == "Contribution à l'étude de la démocratie en Afrique"
    assert c["titre_classement"] == "Contribution à l'étude de la démocratie en Afrique"
    assert c["subtitle"] == ["le cas du Sénégal"]
    assert c["discipline"] == "Science politique"
    assert c["granting_institution"] == {"forme": "École pratique des hautes études", "ppn": "026375478",
                                         "statut_idref": "accepted"}
    assert c["author"] == [{"forme": "Jean-Daniel DUBOIS", "nom": "Dubois", "prenom": "Jean-Daniel",
                            "ppn": "TEST00001", "statut_idref": "accepted"}]
    assert c["advisor"][0]["nom"] == "La Roche" and c["advisor"][0]["ppn"] is None
    assert [r["ppn"] for r in c["reviewers"]] == [None, "P2"]  # low_confidence : pas de PPN
    assert c["jury_president"] == [] and c["doctoral_school"] is None
    assert (c["volumaison"], c["code_etab"], c["numero_nnt"], c["dewey_code"]) == ("1 volume", "EPHE", "XXXX", "320")


def test_contexte_alertes_et_journal():
    m = normaliser_metadonnees(EXTRACTION | {"volume": "2", "defense_year": "vers 1998", "title": "COUT EN $"})
    c = construire(m, "these")
    assert c.valeurs["volumaison"] == "? volumes"
    assert c.valeurs["defense_year"] is None
    assert {"numero_nnt:bouchon", "defense_year:annee_invalide:vers 1998", "dollar_dans_valeur:title",
            "advisor[1]:particule_a_verifier"} <= set(c.alertes)
    t = next(t for t in c.transformations if t.champ == "discipline")
    assert (t.avant, t.apres, t.fonction) == ("SCIENCE POLITIQUE", "Science politique", "casse_phrase")


def test_noms_propres_du_document_preserves():
    m = normaliser_metadonnees(EXTRACTION | {"title": "LA PENSEE DE ROUSSEAU", "committee_members": ["Paul ROUSSEAU"]})
    assert construire(m, "these").valeurs["title"] == "La pensée de Rousseau"
