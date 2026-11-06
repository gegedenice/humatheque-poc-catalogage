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
    assert "Notice déjà dans Alma" in page and 'alma == "présent"' in page and "cas non couvert" in page
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
    texte = ancien.replace('alma == "présent"', 'alma ==')
    r = client.post("/admin/regles/enregistrer", data={"fichier": "decisions.yaml", "texte": texte, "auteur": "C"})
    assert r.status_code == 422 and "Modification refusée" in html.unescape(r.text)
    assert "alma ==" in html.unescape(r.text)  # le brouillon est conservé dans le formulaire
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
