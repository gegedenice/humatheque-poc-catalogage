"""Couche application/ (SPEC §9.1, §11 étape 6), sans HTTP : exécuteur synchrone,
SQLite temporaire, services simulés."""

import csv
import io
import shutil

import pytest
import respx

import services_simules as sim
from humatheque.application import (
    ActionImpossible, Application, EntreeLot, ExecuteurSynchrone, Introuvable, documents_depuis_liste,
)
from humatheque.config import Reglages
from conftest import RACINE
from humatheque.store import Store
from services_simules import EXTRACTION, simuler


@pytest.fixture
def app(tmp_path):
    """Racine isolée : spec/ copiée, data/ vide."""
    shutil.copytree(RACINE / "spec", tmp_path / "spec")
    reglages = Reglages(_env_file=None).model_copy(update={"racine": tmp_path, "spec_source": "csv"})
    return Application(store=Store(tmp_path / "data" / "t.db"), reglages=reglages,
                       fabrique=lambda s, f: sim.services(s, f), executeur=ExecuteurSynchrone())


@pytest.fixture
def image(tmp_path):
    p = tmp_path / "page.png"
    p.write_bytes(sim.png())
    return str(p)


def test_documents_depuis_liste():
    texte = "https://m/TH/a.jpg FAIT\n\n# commentaire\nhttps://m/TH/b.jpg\n"
    assert documents_depuis_liste(texte) == [["https://m/TH/a.jpg"], ["https://m/TH/b.jpg"]]


def test_accueil_etat_de_la_spec(app):
    a = app.accueil()
    assert a.spec.source == "csv" and a.spec.nb_erreurs == 0 and a.spec.nb_avertissements == 2
    assert a.spec.grist_joignable is None
    assert "vlm" in a.fournisseurs and a.lots == []


def test_recharger_spec(app):
    avant = app.etat_spec()
    regles = app.reglages.dossier_spec / "Regles.csv"
    regles.write_text(regles.read_text(encoding="utf-8").replace("105 ##$aa", "105 ##$a"), encoding="utf-8")
    apres = app.recharger_spec()
    assert apres.version.split()[1] != avant.version.split()[1]  # empreinte changée
    assert apres.nb_erreurs == 1 and apres.constats[0].ligne == "R009"


@respx.mock
def test_lot_mode2_indicateurs_et_file(app, image):
    simuler()
    lot = app.creer_lot(EntreeLot(documents=[[image], [image], [image]], source="televersement"))
    assert lot.mode == 2
    # 2e document : notice trouvée dans Alma → sortie ; on remonte les routes après coup
    vue = app.vue_lot(lot.id)
    assert vue.indicateurs.documents == 3 and vue.indicateurs.passes_seuls == 3
    assert not vue.en_cours and vue.file == []
    assert vue.journal[0].texte.startswith("lot ouvert : 3 document(s)")
    assert vue.journal[-1].texte.startswith("lot clos : 3 notice(s)")
    assert {e["etape"]: e["faits"] for e in vue.etapes}["generation"] == 3


@respx.mock
def test_file_d_exceptions_filtres_et_reprise(app, image):
    simuler(alma=sim.alma("academic_record_found", ppn="229969437"))
    lot = app.creer_lot(EntreeLot(documents=[[image], [image]]))
    vue = app.vue_lot(lot.id)
    assert vue.indicateurs.en_attente == 2 and vue.indicateurs.par_routage == {"sortie": 2}
    ligne = vue.file[0]
    assert (ligne.routage, ligne.etape, ligne.etape_libelle) == ("sortie", "situation", "Situation")
    assert "229969437" in ligne.motif
    assert app.vue_lot(lot.id, "incidents").file == []
    assert len(app.vue_lot(lot.id, "sorties").file) == 2

    # Reprise : faux positif Alma sur le premier dossier
    simuler()  # Sudoc et suite répondent normalement
    app.declarer_faux_positif(ligne.dossier_id, "alma", "autre ouvrage", auteur="cat")
    vue = app.vue_lot(lot.id)
    assert vue.indicateurs.repris == 1 and vue.indicateurs.en_attente == 1
    d = app.vue_dossier(ligne.dossier_id)
    assert d.dossier.routage == "auto" and d.dossier.notice_texte

    # Traité hors outil : sort de la file d'attente sans relance
    autre = vue.file[0].dossier_id
    with pytest.raises(ActionImpossible):
        app.traiter_hors_outil(autre, " ", auteur="cat")
    app.traiter_hors_outil(autre, "exemplarisé dans WinIBW", auteur="cat")
    vue = app.vue_lot(lot.id)
    assert vue.indicateurs.en_attente == 0 and vue.file[0].traite


@respx.mock
def test_mode1_vue_dossier_et_telechargements(app, image):
    simuler()
    lot = app.creer_lot(EntreeLot(documents=[[image]], source="televersement"))
    assert lot.mode == 1
    (ligne,) = app.store.lignes_du_lot(lot.id)
    v = app.vue_dossier(ligne.id)
    assert v.etat == "termine" and v.titre == "Contribution à l'étude de la démocratie en Afrique"
    assert [e.nom for e in v.etapes][:3] == ["extraction", "provenance", "alma"]
    assert v.etapes[0].libelle == "Extraction VLM" and v.etapes[0].statut == "ok"
    champ = next(c for c in v.champs if c.champ == "title")
    assert champ.brut == EXTRACTION["title"] and champ.normalise.startswith("Contribution à")
    assert set(v.telechargements.values()) == {"notice.txt", "notice.xml"}
    nom, contenu, mime = app.telecharger(ligne.id, "notice.xml")
    assert nom.endswith(".xml") and b"<record" in contenu and mime == "application/xml"
    with pytest.raises(Introuvable):
        app.telecharger(ligne.id, "source.iso")
    assert v.images[0].url == ""  # image hors de data/uploads : non servie


@respx.mock
def test_conversion_telechargements_iso(app, image):  # ALM-SUD-F-002
    simuler(sudoc=sim.sudoc([("229969437", 0.95, "electronic")], "electronic_only"))
    lot = app.creer_lot(EntreeLot(documents=[[image]]))
    (ligne,) = app.store.lignes_du_lot(lot.id)
    v = app.vue_dossier(ligne.id)
    assert v.dossier.routage == "conversion"
    assert {"source.iso", "convertie.iso", "convertie.txt"} <= set(v.telechargements.values())
    _, iso, _ = app.telecharger(ligne.id, "source.iso")
    assert iso.endswith(b"\x1d")
    assert "029 ##$aFR$b2017EHES0033" in app.export_notices(lot.id)


@respx.mock
def test_resolutions_idref_et_dewey(app, image):
    simuler(idref_statut="low_confidence")
    lot = app.creer_lot(EntreeLot(documents=[[image]]))
    (ligne,) = app.store.lignes_du_lot(lot.id)
    v = app.vue_dossier(ligne.id)
    assert v.dossier.routage == "décision" and v.exceptions_ouvertes == 3
    i = next(i for i, e in enumerate(v.dossier.exceptions) if e.objet.startswith("author"))
    with pytest.raises(ActionImpossible, match="mal formé"):
        app.choisir_ppn(ligne.id, i, "123", auteur="cat")
    v = app.choisir_ppn(ligne.id, i, "026940345", auteur="cat")
    assert "700 #1$3026940345$4070" in v.dossier.notice_texte.splitlines()
    assert v.exceptions_ouvertes == 2
    # « sans lien » pour les deux autres → plus d'exception, routage auto
    for j, e in enumerate(v.dossier.exceptions):
        if not e.resolue:
            v = app.choisir_ppn(ligne.id, j, None, auteur="cat")
    assert v.exceptions_ouvertes == 0 and v.dossier.routage == "auto"
    assert "701 #1$aPetit$bLuc$4958$4555" in v.dossier.notice_texte.splitlines()


@respx.mock
def test_resolution_dewey(app, image):
    simuler()
    d = app.vue_dossier(app.store.lignes_du_lot(app.creer_lot(EntreeLot(documents=[[image]])).id)[0].id).dossier
    # Seuil élevé : la classe devient une exception
    from humatheque.services.dewey import ResultatDewey
    d.dewey = ResultatDewey(statut="sous_seuil", classes=d.dewey.classes)
    from humatheque.workflow.modeles import ExceptionDossier
    d.exceptions.append(ExceptionDossier(etape="dewey", objet="classe Dewey", motif="Dewey : sous_seuil"))
    ligne = app.store.ligne_dossier(d.id)
    app.store.enregistrer_dossier(d, ligne.lot_id, ligne.rang, "termine")
    with pytest.raises(ActionImpossible):
        app.choisir_dewey(d.id, len(d.exceptions) - 1, "123", auteur="cat")
    v = app.choisir_dewey(d.id, len(d.exceptions) - 1, "700", auteur="cat")
    assert "686 ##$a700$2TEF" in v.dossier.notice_texte.splitlines()


@respx.mock
def test_provenance_puis_relance(app, image):
    routes = simuler(extraction=EXTRACTION | {"granting_institution": "École des hautes études"})
    lot = app.creer_lot(EntreeLot(documents=[[image]]))
    (ligne,) = app.store.lignes_du_lot(lot.id)
    assert app.vue_dossier(ligne.id).dossier.etape_arret == "provenance"
    with pytest.raises(ActionImpossible):
        app.choisir_provenance(ligne.id, "peut-être", auteur="cat")
    v = app.choisir_provenance(ligne.id, "hors EPHE", auteur="cat")
    assert v.dossier.decision.situation == "S09" and v.dossier.notice_texte
    app.relancer(ligne.id, depuis="extraction")
    assert routes["vlm"].call_count == 2
    with pytest.raises(ActionImpossible):
        app.relancer(ligne.id, depuis="nulle_part")


def test_erreur_inattendue_dossier_en_incident(app, image):
    def casse(spec, fournisseur):
        raise ValueError("URL du service alma-checker non configurée")
    app.fabrique = casse
    lot = app.creer_lot(EntreeLot(documents=[[image]]))
    d = app.vue_dossier(app.store.lignes_du_lot(lot.id)[0].id)
    assert d.etat == "termine" and d.dossier.routage == "incident"
    assert "non configurée" in d.dossier.motif_arret


@respx.mock
def test_exports_et_historique(app, image):
    simuler()
    lot = app.creer_lot(EntreeLot(documents=[[image], [image]], identifiants=["TH/TH-01", "TH/TH-02"],
                                  source="minio"))
    lignes = list(csv.DictReader(io.StringIO(app.export_csv(lot.id))))
    assert [l["document"] for l in lignes] == ["TH/TH-01", "TH/TH-02"]
    assert lignes[0]["routage"] == "auto" and lignes[0]["situation"] == "S08" and lignes[0]["version_spec"]
    notices = app.export_notices(lot.id)
    assert notices.count("008 $aAax3") == 2
    h = app.historique()
    assert h.ingestions[0].lot_id == lot.id and h.ingestions[0].documents == ["TH/TH-01", "TH/TH-02"]
    assert h.lots[0].lot.id == lot.id
    with pytest.raises(Introuvable):
        app.export_csv("L-inconnu")


def test_televersement(app):
    docs = app.enregistrer_televersement([("p1.png", sim.png()), ("../p 2.png", sim.png())], un_seul_document=True)
    assert len(docs) == 1 and len(docs[0]) == 2
    assert docs[0][1].endswith("002_p_2.png")
    assert app.url_image(docs[0][0]).startswith("/fichiers/")
    rel = app.url_image(docs[0][0]).removeprefix("/fichiers/")
    assert app.chemin_fichier(rel).read_bytes().startswith(b"\x89PNG")
    with pytest.raises(Introuvable):
        app.chemin_fichier("../t.db")
    assert len(app.enregistrer_televersement([("a.png", b"x"), ("b.png", b"y")], un_seul_document=False)) == 2


def test_lot_vide_ou_fournisseur_inconnu(app):
    with pytest.raises(ActionImpossible):
        app.creer_lot(EntreeLot(documents=[]))
    with pytest.raises(ActionImpossible):
        app.creer_lot(EntreeLot(documents=[["x"]], fournisseur="inconnu"))
