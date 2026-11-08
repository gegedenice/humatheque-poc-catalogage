"""Studio de catalogage (SPEC §9.3) : notice structurée (socle + variantes)
éditée par formulaire, vérifications humaines en états configurables, banc
d'essai de bout en bout, non-régression."""

import html

import pytest
import respx

from humatheque.application import ActionImpossible
from humatheque.regles.modeles import ZoneRegle
import services_simules as sim
from services_simules import simuler
from test_web import _televerser, app, client  # noqa: F401  (fixtures)

AUTEUR = {"auteur": "Camille"}


def _txt(r):
    return html.unescape(r.text)


# ---------------------------------------------------------------- pages
@pytest.mark.parametrize("onglet, attendu", [
    ("decisions", "+ Ajouter une règle"), ("verifications", "Sudoc : résultat à qualifier"),
    ("notice", "Socle commun"), ("donnees", "IdRef — personnes"), ("essai", "Non-régression"),
    ("modifier", 'id="editeur-regles"'),
])
def test_onglets_du_studio(client, onglet, attendu):
    r = client.get(f"/admin/regles?onglet={onglet}")
    assert r.status_code == 200 and attendu in _txt(r)


def test_decisions_codes_au_survol_et_etats(client):
    page = _txt(client.get("/admin/regles"))
    assert 'title="codes : academic_record_found"' in page
    assert "⧗ Alma : candidat incertain" in page  # état de vérification de la règle


def test_notice_socle_et_variante(client):
    socle = _txt(client.get("/admin/regles?onglet=notice"))
    assert "Note de thèse" in socle and "Rendu des autorités" in socle
    variante = _txt(client.get("/admin/regles?onglet=notice&variante=these-reproduction-imprimee"))
    assert "modifiée" in variante and "Aperçu de la notice" in variante
    memoire = _txt(client.get("/admin/regles?onglet=notice&variante=memoire-original-imprime"))
    assert "retirée" in memoire


def test_editeur_de_zone_et_brouillon(client):
    r = _txt(client.get("/admin/regles/notice/zone", params={"variante": "socle", "id": "328"}))
    assert "Diplôme" in r and "Utiliser une valeur de repli" in r and 'value="19.."' in r
    assert "Aperçu sur l'exemple" in r and "Thèse de doctorat" in r
    # brouillon : ajout d'une sous-zone, rien n'est écrit
    donnees = {"variante": "socle", "ancien_id": "328", "origine": "socle", "id": "328", "zone": "328",
               "indicateurs": "#0", "libelle": "Note de thèse", "actif": "1", "action": "ajouter_sz",
               "sz-0-code": "b", "sz-0-genre": "variable", "sz-0-variable": "diplome", "sz-0-si_absente": "omettre"}
    r = _txt(client.post("/admin/regles/notice/zone/brouillon", data=donnees))
    assert r.count('name="sz-1-code"') == 1 and "Valeur fixe" in r


def _formulaire_328(**changements):
    d = {"variante": "socle", "ancien_id": "328", "origine": "socle", "id": "328", "zone": "328", "indicateurs": "#0",
         "libelle": "Note de thèse", "priorite": "1", "a_valider": "1", "actif": "1",
         "sz-0-code": "b", "sz-0-genre": "variable", "sz-0-variable": "diplome", "sz-0-si_absente": "omettre",
         "sz-1-code": "c", "sz-1-genre": "variable", "sz-1-variable": "discipline", "sz-1-si_absente": "omettre",
         "sz-2-code": "e", "sz-2-genre": "variable", "sz-2-variable": "etablissement", "sz-2-si_absente": "omettre",
         "sz-3-code": "d", "sz-3-genre": "variable", "sz-3-variable": "annee", "sz-3-si_absente": "repli",
         "sz-3-repli": "19..", "portee": "socle", **AUTEUR}
    d.update(changements)
    return d


def test_enregistrer_zone_dans_le_socle(client, app):
    avant = app.regles.socle.zones
    r = client.post("/admin/regles/notice/zone", data=_formulaire_328(**{"sz-1-si_absente": "verification"}))
    assert r.status_code == 303 and "onglet=notice" in r.headers["location"]
    z = next(z for z in app.regles.socle.zones if z.id == "328")
    assert z.sous_zones[1].si_absente == "verification" and len(app.regles.socle.zones) == len(avant)
    assert "si_absente: verification" in app.texte_regles("notice/socle.yaml")
    # la vérification demandée apparaît à la génération si la discipline manque
    a = app.apercu_profil("these-originale-imprimee")
    assert not a.verifications  # l'exemple a une discipline
    from humatheque.regles.notice import generer
    n = generer(app.regles, "these-originale-imprimee", {**app.exemple_variables(), "discipline": None})
    assert any("328 $c" in v for v in n.verifications)


def test_modifier_pour_une_variante_puis_revenir(client, app):
    r = client.post("/admin/regles/notice/zone", data=_formulaire_328(
        variante="memoire-original-imprime", portee="variante", **{"sz-0-genre": "constante",
                                                                    "sz-0-constante": "Mémoire"}))
    assert r.status_code == 303
    v = app.regles.variantes["memoire-original-imprime"]
    assert "328" in {z.id for z in v.remplacer}
    assert app.regles.profils["memoire-original-imprime"].zones and \
        next(z for z in app.regles.profils["memoire-original-imprime"].zones if z.id == "328").sous_zones[0].constante \
        == "Mémoire"
    assert next(z for z in app.regles.socle.zones if z.id == "328").sous_zones[0].variable == "diplome"
    client.post("/admin/regles/notice/zone/operation", data={"variante": "memoire-original-imprime", "id": "328",
                                                             "operation": "revenir", **AUTEUR})
    assert "328" not in {z.id for z in app.regles.variantes["memoire-original-imprime"].remplacer}


def test_retirer_remettre_ajouter_supprimer(app):
    app.modifier_presence_zone("these-originale-imprimee", "686", "retirer", "C")
    assert "686" not in {z.id for z in app.regles.profils["these-originale-imprimee"].zones}
    app.modifier_presence_zone("these-originale-imprimee", "686", "remettre", "C")
    assert "686" in {z.id for z in app.regles.profils["these-originale-imprimee"].zones}
    zone = ZoneRegle.model_validate({"id": "330-resume", "zone": "330", "indicateurs": "##",
                                     "sous_zones": [{"code": "a", "variable": "discipline"}]})
    app.enregistrer_zone("these-originale-imprimee", None, zone, "variante", "C")
    assert "330-resume" in {z.id for z in app.regles.variantes["these-originale-imprimee"].ajouter}
    assert "330 ##$aBiologie marine" in app.apercu_profil("these-originale-imprimee").texte
    app.modifier_presence_zone("these-originale-imprimee", "330-resume", "supprimer", "C")
    assert "330-resume" not in {z.id for z in app.regles.variantes["these-originale-imprimee"].ajouter}


def test_zone_invalide_refusee(client, app):
    ancien = app.texte_regles("notice/socle.yaml")
    r = client.post("/admin/regles/notice/zone", data=_formulaire_328(**{"sz-0-variable": "inexistante"}))
    assert r.status_code == 422 and "variable inconnue" in _txt(r) and "Enregistrement refusé" in _txt(r)
    assert app.texte_regles("notice/socle.yaml") == ancien
    with pytest.raises(ActionImpossible, match="pas dans le socle"):
        app.enregistrer_zone("socle", "029", ZoneRegle(id="029", zone="029"), "socle", "C")


def test_rendus_d_autorite(client, app):
    client.post("/admin/regles/notice/rendus", data={"rendu.personne": "nom_prenom",
                                                    "rendu.organisme": "ppn_sinon_forme", **AUTEUR})
    assert app.regles.socle.rendus["personne"] == "nom_prenom"
    a = app.apercu_profil("these-originale-imprimee")
    assert "700 #1$aDurand$bMarie$4070" in a.texte and "$3026594803" not in a.texte.split("701")[0]


# ---------------------------------------------------------------- vérifications humaines
def test_editer_un_etat_de_verification(client, app):
    assert "Alma : candidat incertain" in _txt(client.get("/admin/regles/verification", params={"nom": "alma_incertain"}))
    r = client.post("/admin/regles/verification", data={
        "ancien_nom": "", "nom": "titre_douteux", "libelle": "Titre douteux", "question": "Le titre est-il correct ?",
        "c-0-libelle": "Oui, poursuivre", "c-0-effet": "action:création:these-originale-imprimee",
        "c-1-libelle": "Non, arrêter", "c-1-effet": "action:arrêt", **AUTEUR})
    assert r.status_code == 303
    e = app.regles.verifications["titre_douteux"]
    assert [(c.action, c.profil) for c in e.choix] == [("création", "these-originale-imprimee"), ("arrêt", None)]
    # brouillon : ajouter un choix
    r = _txt(client.post("/admin/regles/verification/brouillon", data={
        "nom": "x", "libelle": "X", "c-0-libelle": "a", "c-0-effet": "fait:alma=absent", "action": "ajouter"}))
    assert 'name="c-1-libelle"' in r and 'value="fait:alma=absent" selected' in r
    # un état référencé par une règle ne peut pas disparaître
    r = client.post("/admin/regles/verification/supprimer", data={"ancien_nom": "alma_incertain", **AUTEUR})
    assert r.status_code == 422 and "alma_incertain" in app.regles.verifications


def test_decision_menant_a_un_etat(client, app):
    client.post("/admin/regles/decision", data={"ancien_nom": "Notice déjà dans Alma", "nom": "Notice déjà dans Alma",
                                                "quand.alma": "présent", "alors": "vérification",
                                                "verification": "alma_incertain", **AUTEUR})
    r = app.regle_decision("Notice déjà dans Alma")
    assert (r.alors, r.verification) == ("vérification", "alma_incertain")


@respx.mock
def test_verification_dans_le_dossier_puis_reprise(client, app):
    """Alma incertain → état « Alma : candidat incertain » ; le catalogueur choisit
    « faux positif » → le traitement reprend (Sudoc, décision, notice)."""
    simuler(alma=sim.alma("ambiguous_academic_candidate"))
    dossier = _televerser(client).headers["location"].rsplit("/", 1)[1]
    d = app.store.dossier(dossier)
    assert d.routage == "à reprendre", (d.etape_arret, d.motif_arret)
    assert d.decision.verification == "alma_incertain"
    page = _txt(client.get(f"/dossiers/{dossier}"))
    assert "En attente de vérification" in page and "La notice trouvée dans Alma décrit-elle ce document ?" in page
    r = client.post(f"/dossiers/{dossier}/verification", data={"choix": "1", "commentaire": "autre thèse", **AUTEUR})
    assert r.status_code in (200, 303)
    d = app.store.dossier(dossier)
    assert d.etat_alma == "absent" and d.decision.situation == "Thèse EPHE absente du Sudoc" and d.notice_texte
    assert any(e.type == "resolution" and e.detail.get("choix") == "Non, faux positif" for e in d.journal)
    assert len(d.etapes["alma"].appels) == 0  # Alma non rappelé : choix du catalogueur


@respx.mock
def test_action_imposee_par_le_catalogueur(client, app):
    simuler(alma=sim.alma("ambiguous_academic_candidate"))
    dossier = _televerser(client).headers["location"].rsplit("/", 1)[1]
    app.enregistrer_verification("alma_incertain", app.regles.verifications["alma_incertain"].model_copy(
        update={"choix": [*app.regles.verifications["alma_incertain"].choix,
                          app.regles.verifications["situation_non_prevue"].choix[1]]}), "C")
    app.choisir_verification(dossier, 2, "Camille")
    d = app.store.dossier(dossier)
    assert d.decision.situation == "Choix du catalogueur" and d.decision.profil_cible == "these-originale-imprimee"
    assert d.routage == "terminé" and d.notice_texte


# ---------------------------------------------------------------- banc d'essai, non-régression
def test_banc_d_essai_trois_temps(client):
    r = _txt(client.post("/admin/regles/essai", data={"type": "these", "provenance": "EPHE",
                                                       "alma": "no_academic_record_found", "sudoc": "rien"}))
    assert "Décisions appliquées" in r and "Thèse EPHE absente du Sudoc" in r
    assert "Données préparées" in r and "titre_classement" in r
    assert "Notice prévisualisée" in r and "328 (socle)" in r and "700" in r
    r = _txt(client.post("/admin/regles/essai", data={"type": "these", "provenance": "EPHE", "alma": "présent",
                                                       "sudoc": "non vérifié"}))
    assert "ne produit pas de notice" in r
    r = _txt(client.post("/admin/regles/essai", data={"type": "these", "provenance": "EPHE", "alma": "présent",
                                                       "sudoc": "non vérifié", "profil": "memoire-original-imprime"}))
    assert "memoire-original-imprime</span> (choisie pour l'essai)" in r


@respx.mock
def test_non_regression(client, app):
    simuler()
    _televerser(client)
    r = _txt(client.post("/admin/regles/non-regression"))
    assert "Aucun changement sur les 1 notice(s)" in r
    client.post("/admin/regles/notice/zone", data=_formulaire_328(**{"sz-3-genre": "constante",
                                                                    "sz-3-constante": "2099"}))
    r = _txt(client.post("/admin/regles/non-regression"))
    assert "change la zone 328 sur 1 des 1 notices" in r.replace("<strong>", "").replace("</strong>", "")


@respx.mock
def test_choix_de_provenance_sans_rappel_des_services(client, app):
    routes = simuler(extraction=sim.EXTRACTION | {"granting_institution": "École des hautes études"})
    dossier = _televerser(client).headers["location"].rsplit("/", 1)[1]
    assert app.store.dossier(dossier).decision.verification == "provenance"
    app.choisir_verification(dossier, 1, "Camille")  # Hors EPHE
    d = app.store.dossier(dossier)
    assert d.provenance.valeur == "hors EPHE" and d.decision.situation == "Thèse hors EPHE absente du Sudoc"
    assert routes["alma"].call_count == 1 and routes["sudoc"].call_count == 1 and d.notice_texte
