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
    texte = ancien.replace("status: academic_record_found", "status: academic_record_foundd")
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
    assert [e["etape"] for e in c["etapes"]] == ["alma"] and c["etapes"][-1]["resultat"] == "stop"
    assert c["issue"] == "terminé"
    a = client.post("/api/v1/regles/apercu", json={"profil": "memoire-original-imprime"}).json()
    assert "701 #1$3026594803$4003" in a["texte"]




# ---------------------------------------------------------------- prompts (Administration › Prompts)
def test_page_prompts_et_correspondances(client):
    page = _txt(client.get("/admin/prompts"))
    assert "extraction_these.txt" in page and "transform it into sentence case with French accents" in page
    assert "Du prompt à la notice" in page and "titre_classement" in page and "@ devant le premier mot" in page
    assert "granting_institution" in page and "etablissement" in page
    memoire = _txt(client.get("/admin/prompts", params={"nom": "extraction_memoire.txt"}))
    assert "Mémoire de master recherche 2e année" in memoire
    assert client.get("/admin/prompts", params={"nom": "../.env"}).status_code == 404


def test_modifier_un_prompt(client, app, tmp_path):
    import shutil
    from conftest import RACINE
    shutil.copytree(RACINE / "config", tmp_path / "config")  # ne pas écrire dans le dépôt
    p = app.prompt("extraction_these.txt")
    texte = p.texte.replace("Return ONLY valid JSON.", "Return ONLY valid JSON, nothing else.")
    r = client.post("/admin/prompts", data={"nom": p.nom, "texte": texte, **AUTEUR})
    assert r.status_code == 303 and "enregistre=1" in r.headers["location"]
    assert app.prompt(p.nom).texte == texte and app.prompt(p.nom).empreinte != p.empreinte
    assert app.journal_prompts()[0]["auteur"] == "Camille"
    r = client.post("/admin/prompts", data={"nom": p.nom, "texte": "pas de schéma", **AUTEUR})
    assert r.status_code == 422 and "aucun schéma JSON" in _txt(r) and "pas de schéma" in _txt(r)
    assert (tmp_path / "config" / "prompts" / "archives").is_dir()


# ---------------------------------------------------------------- alertes affichées
ALERTES_ANCIENNES = [
    "granting_institution:accent_ambigu:PARIS", "title:mot_inconnu:MULTIRESOLUTIONS", "numero_nnt:bouchon",
    "jury_president[1] Monsieur Ivar EKELAND : proto-notice d'autorité à créer",
    "jury_president[1] Ivar Ekeland : non trouvé dans IdRef",
    "pipeline:idref:non trouvé dans IdRef|reviewers[2] Jean-Michel Morel",
    "zone 029 obligatoire non produite (029 (these-originale-imprimee))", "conversion_non_implementee",
]


def test_alertes_regroupees_et_anciennes_masquees(regles):
    from humatheque.application.alertes import regrouper
    groupes = {g.libelle: g.elements for g in regrouper(ALERTES_ANCIENNES, regles.alertes)}
    assert groupes == {
        "Non trouvé dans IdRef": ["Ivar Ekeland (président du jury)", "Jean-Michel Morel (rapporteur)"],
        "Zone obligatoire non produite": ["029"],
        "Conversion non implémentée (copie de la notice source)": [],
    }


def test_onglet_alertes(client, app):
    page = _txt(client.get("/admin/regles?onglet=alertes"))
    assert "Anciennes alertes" in page and "proto-notice" in page and 'name="afficher.zone_obligatoire"' in page
    donnees = {f"libelle.{c}": f.libelle for c, f in app.regles.alertes.items()}
    donnees |= {f"afficher.{c}": "1" for c, f in app.regles.alertes.items() if f.afficher and c != "zone_obligatoire"}
    donnees["libelle.pipeline"] = "Pipeline"
    r = client.post("/admin/regles/alertes", data={**donnees, **AUTEUR})
    assert r.status_code == 303
    assert not app.regles.alertes["zone_obligatoire"].afficher and app.regles.alertes["pipeline"].libelle == "Pipeline"
    assert "# Alertes affichées" in app.texte_regles("alertes.yaml")


@respx.mock
def test_alerte_idref_dans_le_dossier(client, app):
    simuler(idref_statut="not_found")
    dossier = _televerser(client).headers["location"]
    page = _txt(client.get(dossier))
    assert "Non trouvé dans IdRef</span> : Jean-Daniel Dubois (auteur)" in page
    assert "pipeline:idref" not in page.split("volet-journal")[0]  # code brut : seulement au journal


def _avec_config(tmp_path):
    import shutil
    from conftest import RACINE
    shutil.copytree(RACINE / "config", tmp_path / "config", dirs_exist_ok=True)


@respx.mock
def test_champ_ajoute_au_prompt_jusqu_a_la_notice(client, app, tmp_path):
    """abstract ajouté au prompt → variable resume activée → zone 330 → notice (ING-T-002, VLM-T-002)."""
    _avec_config(tmp_path)
    p = app.prompt("extraction_these.txt")
    texte = p.texte.replace('"language":', '"abstract": "Abstract of the thesis, if present",\n  "language":')
    assert client.post("/admin/prompts", data={"nom": p.nom, "texte": texte, **AUTEUR}).status_code == 303
    page = _txt(client.get("/admin/prompts"))
    assert 'name="l-' in page and "abstract" in page
    lignes = [c for c in app.correspondances() if c.champ]
    i = next(i for i, c in enumerate(lignes) if c.champ == "abstract")
    donnees = {"nom": p.nom, **AUTEUR}
    for k, c in enumerate(lignes):
        donnees |= {f"l-{k}-champ": c.champ, f"l-{k}-ancien": c.variable or "", f"l-{k}-variable": c.variable or "",
                    f"l-{k}-type": c.type}
        if c.liste:
            donnees[f"l-{k}-liste"] = "1"
        if c.disponible or k == i:
            donnees[f"l-{k}-disponible"] = "1"
    assert client.post("/admin/prompts/variables", data=donnees).status_code == 303
    assert app.regles.variables["resume"].disponible
    # zone 330 activée dans le socle, liée à la variable resume
    from humatheque.regles.modeles import ZoneRegle
    z = ZoneRegle.model_validate({"id": "330", "zone": "330", "libelle": "Résumé", "indicateurs": "##",
                                  "sous_zones": [{"code": "a", "variable": "resume"}]})
    app.enregistrer_zone("socle", "330", z, "socle", "C")
    import services_simules as sim
    from humatheque.contexte import construire, normaliser_metadonnees, variables_profil
    from humatheque.regles.notice import generer
    m = normaliser_metadonnees(sim.EXTRACTION | {"abstract": "Cette thèse étudie la démocratie."})
    v = variables_profil(construire(m, "these").valeurs, app.regles)
    assert "330 ##$aCette thèse étudie la démocratie." in [x.texte() for x in generer(app.regles, "these-originale-imprimee", v).zones]


def test_nouvelle_variable_pour_un_champ_nouveau(client, app):
    lignes = [c for c in app.correspondances() if c.champ]
    donnees = {"nom": "extraction_these.txt", **AUTEUR}
    for k, c in enumerate(lignes):
        donnees |= {f"l-{k}-champ": c.champ, f"l-{k}-ancien": c.variable or "", f"l-{k}-variable": c.variable or "",
                    f"l-{k}-type": c.type, **({f"l-{k}-disponible": "1"} if c.disponible else {}),
                    **({f"l-{k}-liste": "1"} if c.liste else {})}
    k = len(lignes)
    donnees |= {f"l-{k}-champ": "keywords", f"l-{k}-variable": "mots_cles", f"l-{k}-liste": "1",
                f"l-{k}-disponible": "1"}
    assert client.post("/admin/prompts/variables", data=donnees).status_code == 303
    v = app.regles.variables["mots_cles"]
    assert (v.source, v.liste, v.disponible) == ("extraction.keywords", True, True)
    # renommer une variable utilisée dans la notice : refusé par le vérificateur
    j = next(j for j, c in enumerate(lignes) if c.champ == "subtitle")  # sous_titre : utilisée en 200 $e
    donnees[f"l-{j}-variable"] = "sous_titre_renomme"
    r = client.post("/admin/prompts/variables", data=donnees)
    assert r.status_code == 422 and "variable inconnue" in _txt(r)


def test_feuille_de_style_versionnee(client):
    page = client.get("/admin/prompts").text
    assert '/static/css/app.css?v=' in page and 'style="height: 65vh' in page


# ---------------------------------------------------------------- ordre des étapes (blocs)
def test_deplacer_un_bloc_change_l_ordre_d_execution(client, app):
    """Décision du 11/11 : les étapes se déplacent dans leur groupe ; l'ordre du fichier
    est l'ordre d'exécution du workflow."""
    from humatheque.workflow.modeles import ordre_etapes

    assert list(app.regles.etapes)[:3] == ["alma", "provenance", "sudoc"]
    page = _txt(client.get("/admin/regles?onglet=pipeline"))
    assert "Monter l'étape Provenance" in page
    r = client.post("/admin/regles/etape/operation", data={"etape": "provenance", "operation": "monter", **AUTEUR})
    assert r.status_code == 303
    assert list(app.regles.etapes)[:3] == ["provenance", "alma", "sudoc"]
    assert ordre_etapes(list(app.regles.etapes))[1:4] == ["provenance", "alma", "sudoc"]
    # hors de son groupe : refusé
    with pytest.raises(ActionImpossible, match="groupe"):
        app.deplacer_etape("sudoc", "descendre", "C")
    with pytest.raises(ActionImpossible, match="groupe"):
        app.deplacer_etape("provenance", "monter", "C")
    app.deplacer_etape("dewey", "monter", "C")
    assert list(app.regles.etapes)[3:] == ["dewey", "idref", "creation"]
    assert ordre_etapes(list(app.regles.etapes))[-4:] == ["dewey", "idref_personnes", "idref_organisations",
                                                           "generation"]
