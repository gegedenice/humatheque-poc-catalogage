"""Administration des règles (SPEC §9.3, page « Règles ») : consultation, banc
d'essai, aperçu, vérification d'un brouillon, enregistrement (archive, journal,
rechargement) et refus d'un brouillon en erreur ; API /api/v1/regles."""

import html

import pytest
import respx

from humatheque.application import ActionImpossible, ReglesRefusees
from services_simules import simuler
from test_web import _televerser, app, client  # noqa: F401  (fixtures)

PROFIL = "profils/these-originale-imprimee.yaml"


def test_page_regles_onglets(client):
    for onglet, attendu in [("decisions", "Banc d'essai"), ("profils", "Aperçu de la notice"),
                            ("variables", "extraction.defense_year"), ("modifier", 'id="editeur-regles"')]:
        r = client.get(f"/admin/regles?onglet={onglet}")
        assert r.status_code == 200 and attendu in html.unescape(r.text), onglet
    page = html.unescape(client.get("/admin/regles").text)
    assert "Notice déjà dans Alma" in page and "cas non couvert" in page and "+ Ajouter une règle" in page
    assert "Règles de catalogage" in client.get("/admin").text  # sous-menu des deux pages


def test_banc_d_essai(client):
    r = client.post("/admin/regles/essai", data={"type": "thèse", "provenance": "hors EPHE", "alma": "absent",
                                                "sudoc": "originale imprimée"})
    texte = html.unescape(r.text)
    assert r.status_code == 200 and "Thèse hors EPHE, originale imprimée au Sudoc" in texte and "conversion" in texte
    assert texte.count("✓") >= 1 and "these-reproduction-imprimee" in texte
    r = client.post("/admin/regles/essai", data={"type": "thèse", "provenance": "EPHE", "alma": "absent",
                                                "sudoc": "originale imprimée"})
    assert "sinon" in r.text


@respx.mock
def test_banc_d_essai_et_apercu_depuis_un_dossier(client, app):
    simuler()
    dossier = _televerser(client).headers["location"].rsplit("/", 1)[1]
    assert app.faits_dossier(dossier)["sudoc"] == "rien"
    r = client.post("/admin/regles/essai", data={"dossier": dossier})
    assert "Thèse EPHE absente du Sudoc" in r.text and "dossier" in r.text
    r = client.post("/admin/regles/apercu", data={"profil": "these-originale-imprimee", "dossier": dossier})
    assert r.status_code == 200 and "700" in r.text and "Variables du contexte" in html.unescape(r.text)


def test_apercu_sur_l_exemple(client):
    r = client.post("/admin/regles/apercu", data={"profil": "these-originale-imprimee"})
    texte = html.unescape(r.text)
    assert "exemple" in texte and "Marie" in texte and "sous la direction de Jean Martin et Anne Petit" in texte
    assert client.post("/admin/regles/apercu", data={"profil": "inconnu"}).status_code == 404


def test_verifier_un_brouillon(client, app):
    texte = app.texte_regles(PROFIL).replace("101 0#$a{langue}", "101 0#$a{langue_x}")
    r = client.post("/admin/regles/verifier", data={"fichier": PROFIL, "texte": texte})
    assert r.status_code == 200 and "erreur(s) bloquante(s)" in r.text and "langue_x" in r.text
    assert "langue_x" not in app.texte_regles(PROFIL)  # rien d'écrit


def test_enregistrer_archive_journalise_recharge(client, app):
    avant = app.regles.version
    ancien = app.texte_regles(PROFIL)
    texte = ancien.replace('remarque: "repli à harmoniser avec 214 et 328"', 'remarque: "modifié par le test"')
    assert texte != ancien
    r = client.post("/admin/regles/enregistrer", data={"fichier": PROFIL, "texte": texte, "auteur": "Camille"})
    assert r.status_code == 303 and "enregistre=archives/" in r.headers["location"]
    assert app.texte_regles(PROFIL) == texte and app.regles.version != avant
    (j,) = app.journal_regles()
    assert (j.auteur, j.fichier) == ("Camille", PROFIL)
    assert (app.reglages.dossier_regles / j.archive).read_text(encoding="utf-8") == ancien
    page = client.get(r.headers["location"]).text
    assert "Enregistré et rechargé" in html.unescape(page) and "Camille" in page


def test_enregistrer_refuse_un_brouillon_en_erreur(client, app):
    ancien = app.texte_regles("decisions.yaml")
    texte = ancien.replace("quand: {alma: présent}", "si: alma ==")
    r = client.post("/admin/regles/enregistrer", data={"fichier": "decisions.yaml", "texte": texte, "auteur": "C"})
    assert r.status_code == 422 and "Modification refusée" in html.unescape(r.text)
    assert "si: alma ==" in html.unescape(r.text)  # le brouillon est conservé dans le formulaire
    assert app.texte_regles("decisions.yaml") == ancien and app.journal_regles() == []
    with pytest.raises(ReglesRefusees):
        app.enregistrer_regles("decisions.yaml", "regles: [\n", "C")


def test_fichier_hors_regles_refuse(app):
    for f in ["../spec/Statuts_services.csv", "profils/nouveau.yaml", "/etc/passwd"]:
        with pytest.raises(ActionImpossible, match="inconnu"):
            app.texte_regles(f)
    with pytest.raises(ActionImpossible, match="aucune modification"):
        app.enregistrer_regles(PROFIL, app.texte_regles(PROFIL), "C")


def test_api_regles(client):
    r = client.get("/api/v1/regles").json()
    assert set(r["fichiers"]) >= {"variables.yaml", "decisions.yaml", PROFIL} and r["version"]
    assert r["rapport"]["nb_erreurs"] == 0
    c = client.post("/api/v1/regles/decision", json={"faits": {"type": "thèse", "provenance": "EPHE",
                                                               "alma": "présent", "sudoc": "non vérifié"}}).json()
    assert c["regle"]["nom"] == "Notice déjà dans Alma" and c["issue"] == "terminé"
    a = client.post("/api/v1/regles/apercu", json={"profil": "memoire-original-imprime"}).json()
    assert "701 #1$3026594803$4003" in a["texte"]


# ---------------------------------------------------------------- dictionnaire et tableau de décision
def test_onglet_faits_et_sorties(client):
    page = html.unescape(client.get("/admin/regles?onglet=faits").text)
    for attendu in ["academic_record_found", "printed_original", "tout code non listé", "aucun code retenu",
                    "IdRef — personnes", "IdRef — structures", "low_confidence", "not_found",
                    "proto-notice d'autorité à créer", "au_dessus_seuil", "aucune_classe", "non bloquant"]:
        assert attendu in page, attendu


def test_banc_d_essai_codes_bruts(client):
    """Codes renvoyés par l'API saisis tels quels : traduits avant décision."""
    r = client.post("/admin/regles/essai", data={"type": "these", "provenance": "hors EPHE",
                                                "alma": "no_academic_record_found", "sudoc": "printed_original"})
    texte = html.unescape(r.text)
    assert "Thèse hors EPHE, originale imprimée au Sudoc" in texte
    assert "printed_original" in texte and "originale imprimée" in texte and "no_academic_record_found" in texte
    r = client.post("/admin/regles/essai", data={"type": "these", "provenance": "EPHE", "alma": "statut_fantome",
                                                "sudoc": ""})
    assert "code inconnu" in html.unescape(r.text) and "sinon" in r.text


def test_formulaire_ajouter_modifier_deplacer_supprimer(client, app):
    assert "Nouvelle règle" in html.unescape(client.get("/admin/regles/decision").text)
    f = html.unescape(client.get("/admin/regles/decision", params={"nom": "Thèse EPHE, reproduction au Sudoc"}).text)
    assert 'value="reproduction microfiche" checked' in f
    n = len(app.regles.decisions)
    # ajout en 2e position
    r = client.post("/admin/regles/decision", data={
        "ancien_nom": "", "nom": "Mémoire, originale au Sudoc", "position": "2", "quand.type": "mémoire",
        "quand.sudoc": ["originale imprimée", "reproduction imprimée"], "alors": "vérification",
        "motif": "à examiner", "a_valider": "1", "auteur": "Camille"})
    assert r.status_code == 303 and "onglet=decisions&enregistre=archives/" in r.headers["location"]
    regle = app.regles.decisions[1]
    assert (regle.nom, regle.quand, regle.a_valider) == (
        "Mémoire, originale au Sudoc", {"type": "mémoire", "sudoc": ["originale imprimée", "reproduction imprimée"]},
        True) and len(app.regles.decisions) == n + 1
    texte = app.texte_regles("decisions.yaml")
    assert "quand: {type: mémoire, sudoc: [originale imprimée, reproduction imprimée]}" in texte
    assert "printed_original" in texte and "# Règles de décision" in texte  # dictionnaire et en-tête conservés
    # deux cas non couverts de moins (mémoire + originale / reproduction imprimée)
    assert sum(c.code == "cas_non_couvert" for c in app._rapport.constats) == 2
    # modification
    client.post("/admin/regles/decision", data={"ancien_nom": "Mémoire, originale au Sudoc",
                                                "nom": "Mémoire, imprimé au Sudoc", "quand.type": "mémoire",
                                                "quand.sudoc": "originale imprimée", "alors": "arrêt", "profil": TO_,
                                                "auteur": "Camille"})
    regle = app.regles.decisions[1]
    assert (regle.nom, regle.quand, regle.alors, regle.profil) == ("Mémoire, imprimé au Sudoc",
                                                                    {"type": "mémoire", "sudoc": "originale imprimée"},
                                                                    "arrêt", None)  # arrêt : pas de profil
    # déplacement
    client.post("/admin/regles/decision/deplacer", data={"nom": "Mémoire, imprimé au Sudoc", "sens": "-1",
                                                         "auteur": "Camille"})
    assert app.regles.decisions[0].nom == "Mémoire, imprimé au Sudoc"
    # sinon
    client.post("/admin/regles/decision", data={"ancien_nom": "sinon", "alors": "vérification",
                                                "motif": "Cas imprévu", "auteur": "Camille"})
    assert app.regles.sinon == {"alors": "vérification", "motif": "Cas imprévu"}
    # suppression
    client.post("/admin/regles/decision/supprimer", data={"nom": "Mémoire, imprimé au Sudoc", "auteur": "Camille"})
    assert len(app.regles.decisions) == n and len(app.journal_regles()) == 5


def test_formulaire_refuse_une_regle_invalide(client, app):
    ancien = app.texte_regles("decisions.yaml")
    r = client.post("/admin/regles/decision", data={"ancien_nom": "", "nom": "Création sans profil",
                                                    "quand.type": "thèse", "alors": "création", "auteur": "C"})
    assert r.status_code == 422 and "profil inconnu" in html.unescape(r.text)
    assert app.texte_regles("decisions.yaml") == ancien
    with pytest.raises(ActionImpossible, match="réservé"):
        app.enregistrer_decision(None, {"nom": "sinon", "alors": "arrêt"}, "C")


@respx.mock
def test_chaine_code_fait_regle_dans_le_dossier(client, app):
    simuler()
    dossier = _televerser(client).headers["location"].rsplit("/", 1)[1]
    d = app.store.dossier(dossier)
    assert d.decision.codes["alma"] == "no_academic_record_found" and d.decision.codes["type"] == "these"
    page = html.unescape(client.get(f"/dossiers/{dossier}").text)
    assert "Code reçu" in page and "no_academic_record_found" in page and "Thèse EPHE absente du Sudoc" in page
    assert "type : thèse ; provenance : EPHE ; sudoc : rien" in page


TO_ = "these-originale-imprimee"


def test_variables_source_et_exemple(client):
    page = html.unescape(client.get("/admin/regles?onglet=variables").text)
    assert "<th>Exemple</th>" in page and "Biologie marine" in page and "forme Marie Durand" in page
    profil = html.unescape(client.get("/admin/regles?onglet=profils&profil=these-originale-imprimee").text)
    assert 'title="discipline — source : ' in profil and "exemple : Biologie marine" in profil
