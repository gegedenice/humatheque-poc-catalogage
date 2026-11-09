"""Administration des règles (SPEC §9.3, studio) : pipeline en arbre édité branche
par branche, banc d'essai, aperçu, vérification d'un brouillon, enregistrement
(archive, journal, rechargement) et refus d'un brouillon en erreur ; API /api/v1/regles."""

import html

import pytest
import respx

from humatheque.application import ActionImpossible, ReglesRefusees
from services_simules import simuler
from test_web import _televerser, app, client  # noqa: F401  (fixtures)

PROFIL = "notice/socle.yaml"
AUTEUR = {"auteur": "Camille"}


def _txt(r):
    return html.unescape(r.text)


# ---------------------------------------------------------------- pages
@pytest.mark.parametrize("onglet, attendu", [
    ("pipeline", "Le pipeline, étape par étape"), ("notice", "Socle commun"), ("essai", "Non-régression"),
    ("modifier", 'id="editeur-regles"'), ("decisions", "Le pipeline, étape par étape"),  # ancien lien
])
def test_onglets(client, onglet, attendu):
    r = client.get(f"/admin/regles?onglet={onglet}")
    assert r.status_code == 200 and attendu in _txt(r)
    assert "Règles de catalogage" in client.get("/admin").text  # sous-menu des deux pages


def test_arbre_du_pipeline(client):
    """Les étapes dans l'ordre, les codes de l'API tels quels, ET / OU / SINON, étape suivante."""
    page = _txt(client.get("/admin/regles?onglet=pipeline"))
    for attendu in ["Provenance", "Alma", "Sudoc", "IdRef", "Dewey", "Création de la notice",
                    "academic_record_found", "ambiguous_academic_candidate", "off_profile_match_only",
                    "duplicate_found", "printed_reproduction", "electronic_only", "no_print_duplicate_found",
                    "low_confidence", "not_found", "CONVERSION these-reproduction-imprimee puis STOP",
                    "STOP à reprendre", "POURSUITE avec alerte", "→ Sudoc", "SINON", "TOUJOURS"]:
        assert attendu in page, attendu
    assert ">ET<" in page and " OU " in page


# ---------------------------------------------------------------- édition de l'arbre
def test_formulaire_d_une_branche(client):
    r = _txt(client.get("/admin/regles/noeud", params={"etape": "sudoc", "chemin": "0.0.1", "operation": "modifier"}))
    assert "Modifier la branche" in r and 'value="printed_reproduction" checked' in r and 'value="oui" checked' in r
    r = _txt(client.get("/admin/regles/noeud", params={"etape": "sudoc", "chemin": "0", "operation": "modifier"}))
    assert "sous-branches (inchangées)" in r  # embranchement : son résultat est porté par ses sous-branches
    r = _txt(client.get("/admin/regles/noeud", params={"etape": "alma", "chemin": "s", "operation": "modifier"}))
    assert "« sinon »" in r and 'name="v.status"' not in r


def test_modifier_ajouter_deplacer_supprimer(client, app):
    # modifier : printed_reproduction + EPHE = non → stop (cas que la spécification ne couvrait pas)
    r = client.post("/admin/regles/noeud", data={
        "etape": "sudoc", "chemin": "0.0", "operation": "sous_branche", "v.thesis_state": "printed_reproduction",
        "v.ephe": "non", "resultat": "stop", "motif": "reproduction déjà au Sudoc", **AUTEUR})
    assert r.status_code == 303 and "onglet=pipeline" in r.headers["location"]
    from humatheque.regles.moteur import evaluer
    v = {"type": "these", "ephe": "non", "status": "duplicate_found", "thesis_state": "printed_reproduction"}
    assert evaluer(app.regles, "sudoc", v).resultat == "stop"
    assert "printed_reproduction" in app.texte_regles("pipeline.yaml") and "# Pipeline de traitement" in \
        app.texte_regles("pipeline.yaml")  # en-tête conservé
    # modifier une branche existante (SAUF) et ajouter une branche après
    client.post("/admin/regles/noeud", data={"etape": "alma", "chemin": "0", "operation": "modifier",
                                            "v.status": "academic_record_found", "mode.type": "sauf",
                                            "v.type": "memoire", "resultat": "stop", **AUTEUR})
    n = app.regles.etapes["alma"].branches[0]
    assert (n.si, n.sauf) == ({"status": "academic_record_found"}, {"type": "memoire"})
    assert n.condition == "status = academic_record_found ET SAUF type = memoire"
    client.post("/admin/regles/noeud", data={"etape": "alma", "chemin": "0", "operation": "ajouter",
                                            "v.status": "academic_record_found", "v.type": "memoire",
                                            "resultat": "stop à reprendre", **AUTEUR})
    assert app.regles.etapes["alma"].branches[1].resultat == "stop à reprendre"
    # déplacer, supprimer
    client.post("/admin/regles/noeud/operation", data={"etape": "alma", "chemin": "1", "operation": "monter", **AUTEUR})
    assert app.regles.etapes["alma"].branches[0].resultat == "stop à reprendre"
    client.post("/admin/regles/noeud/operation", data={"etape": "alma", "chemin": "0", "operation": "supprimer",
                                                      **AUTEUR})
    assert len(app.regles.etapes["alma"].branches) == 3
    # sinon d'une étape
    client.post("/admin/regles/noeud", data={"etape": "alma", "chemin": "s", "operation": "modifier",
                                            "resultat": "stop à reprendre", "motif": "statut inconnu", **AUTEUR})
    assert app.regles.etapes["alma"].sinon.motif == "statut inconnu"
    assert len(app.journal_regles()) == 6


def test_branche_invalide_refusee(client, app):
    ancien = app.texte_regles("pipeline.yaml")
    r = client.post("/admin/regles/noeud", data={"etape": "creation", "chemin": "0", "operation": "modifier",
                                                "v.type": "these", "resultat": "création", "notice": "", **AUTEUR})
    assert r.status_code == 422 and "notice cible inconnue" in _txt(r)
    assert app.texte_regles("pipeline.yaml") == ancien
    with pytest.raises(ActionImpossible, match="sinon"):
        app.modifier_arbre("alma", "s", "supprimer", "C")


# ---------------------------------------------------------------- banc d'essai
def test_banc_d_essai_parcours(client):
    r = _txt(client.post("/admin/regles/essai", data={
        "type": "these", "ephe": "non", "alma.status": "no_academic_record_found",
        "sudoc.status": "duplicate_found", "sudoc.thesis_state": "printed_original"}))
    assert "Parcours du pipeline" in r and "conversion these-reproduction-imprimee" in r
    assert "thesis_state=printed_original" in r and "Notice prévisualisée" in r
    r = _txt(client.post("/admin/regles/essai", data={
        "type": "these", "ephe": "oui", "alma.status": "no_academic_record_found",
        "sudoc.status": "no_print_duplicate_found", "idref.status": "not_found", "dewey.status": "au_dessus_seuil"}))
    for etape in ["Provenance", "Alma", "Sudoc", "IdRef", "Dewey", "Création"]:
        assert etape in r, etape
    assert "poursuite avec alerte" in r and "création these-originale-imprimee" in r and "328 (socle)" in r


@respx.mock
def test_banc_d_essai_depuis_un_dossier(client, app):
    simuler()
    dossier = _televerser(client).headers["location"].rsplit("/", 1)[1]
    assert app.valeurs_dossier(dossier)["sudoc.status"] == "no_print_duplicate_found"
    r = _txt(client.post("/admin/regles/essai", data={"dossier": dossier}))
    assert "création these-originale-imprimee" in r and "dossier" in r
    page = _txt(client.get(f"/dossiers/{dossier}"))
    assert "Parcours du pipeline" in page and "no_academic_record_found" in page


def test_apercu_sur_l_exemple(client):
    r = client.post("/admin/regles/apercu", data={"profil": "these-originale-imprimee"})
    texte = html.unescape(r.text)
    assert "exemple" in texte and "Marie" in texte and "sous la direction de Jean Martin et Anne Petit" in texte
    assert client.post("/admin/regles/apercu", data={"profil": "inconnu"}).status_code == 404


def test_verifier_un_brouillon(client, app):
    texte = app.texte_regles(PROFIL).replace("variable: langue\n", "variable: langue_x\n", 1)
    r = client.post("/admin/regles/verifier", data={"fichier": PROFIL, "texte": texte})
    assert r.status_code == 200 and "erreur(s) bloquante(s)" in r.text and "langue_x" in r.text
    assert "langue_x" not in app.texte_regles(PROFIL)  # rien d'écrit


def test_enregistrer_archive_journalise_recharge(client, app):
    avant = app.regles.version
    ancien = app.texte_regles(PROFIL)
    texte = ancien.replace("remarque: repli à harmoniser avec 214 et 328", "remarque: modifié par le test")
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
    ancien = app.texte_regles("pipeline.yaml")
    texte = ancien.replace("si: {status: academic_record_found}", "si: {status: academic_record_foundd}")
    r = client.post("/admin/regles/enregistrer", data={"fichier": "pipeline.yaml", "texte": texte, "auteur": "C"})
    assert r.status_code == 422 and "Modification refusée" in html.unescape(r.text)
    assert "academic_record_foundd" in html.unescape(r.text)  # le brouillon est conservé dans le formulaire
    assert app.texte_regles("pipeline.yaml") == ancien and app.journal_regles() == []
    with pytest.raises(ReglesRefusees):
        app.enregistrer_regles("pipeline.yaml", "etapes: [\n", "C")


def test_fichier_hors_regles_refuse(app):
    for f in ["../spec/Statuts_services.csv", "notice/variantes/nouvelle.yaml", "/etc/passwd"]:
        with pytest.raises(ActionImpossible, match="inconnu"):
            app.texte_regles(f)
    with pytest.raises(ActionImpossible, match="aucune modification"):
        app.enregistrer_regles(PROFIL, app.texte_regles(PROFIL), "C")


def test_api_regles(client):
    r = client.get("/api/v1/regles").json()
    assert set(r["fichiers"]) >= {"variables.yaml", "pipeline.yaml", PROFIL} and r["version"]
    assert r["rapport"]["nb_erreurs"] == 0
    c = client.post("/api/v1/regles/pipeline", json={"valeurs": {
        "type": "these", "ephe": "oui", "alma.status": "academic_record_found"}}).json()
    assert [e["etape"] for e in c["etapes"]] == ["provenance", "alma"] and c["etapes"][-1]["resultat"] == "stop"
    assert c["issue"] == "terminé"
    a = client.post("/api/v1/regles/apercu", json={"profil": "memoire-original-imprime"}).json()
    assert "701 #1$3026594803$4003" in a["texte"]


