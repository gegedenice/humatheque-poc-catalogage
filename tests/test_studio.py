"""Studio de catalogage (SPEC §9.3), onglet « Notice UNIMARC » : notice structurée
(socle + variantes) éditée par formulaire ; non-régression."""

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


# ---------------------------------------------------------------- notice UNIMARC
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


# ---------------------------------------------------------------- non-régression
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
