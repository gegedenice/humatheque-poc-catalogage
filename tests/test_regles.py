"""Chargement et vérificateur des règles (SPEC §8.1, §8.4, §11 étape 1).

Règles réelles : aucune erreur et exactement les 4 cas non couverts ; puis un
fichier fabriqué par défaut du tableau du §8.4.
"""

import pytest

from humatheque.regles import ReglesInvalides, charger_regles, verifier
from humatheque.regles.verificateur import verifier_textes


def _codes(rapport, gravite=None):
    return [c.code for c in rapport.constats if gravite is None or c.gravite == gravite]


# ---------------------------------------------------------------- règles réelles
def test_chargement_regles_reelles(regles):
    assert set(regles.profils) == {"these-originale-imprimee", "these-reproduction-imprimee",
                                   "memoire-original-imprime"}
    assert len(regles.socle.zones) == 34 and set(regles.verifications) >= {"alma_incertain", "provenance"}
    assert regles.decisions[0].nom == "Notice déjà dans Alma" and regles.sinon["alors"] == "vérification"
    assert regles.variables["auteur"].type == "personne" and regles.variables["directeur"].liste
    assert not regles.variables["numero_nnt"].disponible
    assert len(regles.empreinte) == 12 and regles.version.endswith(regles.empreinte)
    z = regles.profils["these-originale-imprimee"].zones[0]
    assert (z.id, z.zone, z.trace, z.statut, z.indicateurs) == ("008", "008", "008 (socle)", "validée", None)
    z328 = next(z for z in regles.socle.zones if z.id == "328")
    assert [(sz.code, sz.genre, sz.presence) for sz in z328.sous_zones][-1] == ("d", "variable", "repli « 19.. » si absente")


def test_aucune_erreur_et_quatre_cas_non_couverts(regles):
    r = verifier(regles)
    assert not r.erreurs
    trous = [c.message for c in r.constats if c.code == "cas_non_couvert"]
    assert len(trous) == 4
    assert any("type = thèse, provenance = EPHE, sudoc = originale imprimée" in t for t in trous)
    assert sum("type = mémoire" in t for t in trous) == 3
    assert len(r.avertissements) == 4  # les cas non couverts, rien d'autre
    assert {c.code for c in r.constats if c.gravite == "info"} == {"variable_inutilisee"}


def test_empreinte_stable_et_sensible(regles_fabriquees):
    a, b = regles_fabriquees.charger(), regles_fabriquees.charger()
    assert a.empreinte == b.empreinte
    regles_fabriquees.modifier_decision("Thèse EPHE absente du Sudoc", motif="autre motif")
    assert regles_fabriquees.charger().empreinte != a.empreinte


# ---------------------------------------------------------------- défauts fabriqués (§8.4)
def test_yaml_invalide_avec_ligne(regles_fabriquees):
    regles_fabriquees.ecrire("decisions.yaml", regles_fabriquees.texte("decisions.yaml") + "\n  - {id: X, si: [\n")
    with pytest.raises(ReglesInvalides) as e:
        regles_fabriquees.charger()
    (fichier, ligne, message), = e.value.problemes
    assert fichier == "decisions.yaml" and ligne.startswith("ligne ") and "YAML invalide" in message


def test_structure_absente(regles_fabriquees):
    regles_fabriquees.ecrire("notice/socle.yaml", "zone: x\n")
    with pytest.raises(ReglesInvalides, match="clés rendus et zones attendues"):
        regles_fabriquees.charger()
    regles_fabriquees.ecrire("notice/socle.yaml", "zones: [{id: 1, zone: '100', inconnue: 2}]\n")
    with pytest.raises(ReglesInvalides, match="inconnue"):
        regles_fabriquees.charger()


def test_sinon_obligatoire(regles_fabriquees):
    d = regles_fabriquees.lire("decisions.yaml")
    del d["sinon"]
    regles_fabriquees.ecrire("decisions.yaml", d)
    with pytest.raises(ReglesInvalides, match="sinon"):
        regles_fabriquees.charger()


@pytest.mark.parametrize("zone, code", [
    ({"id": "x", "zone": "10", "sous_zones": [{"code": "a", "constante": "X"}]}, "zone_illisible"),
    ({"id": "x", "zone": "330", "indicateurs": "###", "sous_zones": [{"code": "a", "constante": "X"}]},
     "indicateurs_invalides"),
    ({"id": "x", "zone": "330", "indicateurs": "##", "sous_zones": [{"code": "a", "variable": "inexistante"}]},
     "variable_inconnue"),
    ({"id": "x", "zone": "330", "indicateurs": "##", "sous_zones": [{"code": "a", "variable": "titre", "attribut": "ppn"}]},
     "attribut_impossible"),
    ({"id": "x", "zone": "701", "indicateurs": "#1", "sous_zones": [{"code": "3", "autorite": "directeur"},
                                                                     {"code": "3", "autorite": "rapporteur"}]},
     "repetition_multiple"),
    ({"id": "x", "zone": "330", "indicateurs": "##", "sous_zones": [{"code": "a", "autorite": "titre"}]},
     "autorite_invalide"),
    ({"id": "x", "zone": "330", "indicateurs": "##", "sous_zones": [{"code": "a", "variable": "titre",
                                                                     "constante": "X"}]}, "valeur_ambigue"),
    ({"id": "x", "zone": "330", "indicateurs": "##", "sous_zones": [{"code": "a", "variable": "titre",
                                                                     "si_absente": "repli"}]}, "repli_absent"),
    ({"id": "x", "zone": "330", "indicateurs": "##", "sous_zones": [{"code": "a"}]}, "valeur_absente"),
    ({"id": "x", "zone": "700", "indicateurs": "#1", "sous_zones": [{"code": "3", "autorite": "auteur",
                                                                     "rendu": "fantaisie"}]}, "rendu_inconnu"),
    ({"id": "328", "zone": "328", "sous_zones": [{"code": "a", "constante": "X"}]}, "zone_doublon"),
])
def test_defauts_de_zone(regles_fabriquees, zone, code):
    regles_fabriquees.ajouter_zone("socle", zone)
    assert code in _codes(verifier(regles_fabriquees.charger()), "erreur")


def test_defauts_de_variante(regles_fabriquees):
    v = regles_fabriquees.lire("notice/variantes/memoire-original-imprime.yaml")
    v["retirer"].append("999")
    v["remplacer"].append({"id": "998", "zone": "998", "sous_zones": [{"code": "a", "constante": "X"}]})
    v.setdefault("ajouter", []).append({"id": "328", "zone": "328", "sous_zones": [{"code": "a", "constante": "X"}]})
    regles_fabriquees.ecrire("notice/variantes/memoire-original-imprime.yaml", v)
    codes = _codes(verifier(regles_fabriquees.charger()), "erreur")
    assert codes.count("zone_inconnue") == 2 and "zone_doublon" in codes


def test_defauts_de_verification(regles_fabriquees):
    e = regles_fabriquees.lire("verifications.yaml")
    e["provenance"]["choix"].append({"libelle": "x", "faits": {"provenance": "Mars"}})
    e["provenance"]["choix"].append({"libelle": "y", "action": "création", "profil": "fantome"})
    e["provenance"]["choix"].append({"libelle": "z", "faits": {"alma": "absent"}, "action": "arrêt"})
    regles_fabriquees.ecrire("verifications.yaml", e)
    regles_fabriquees.modifier_decision("Candidat Alma à vérifier", verification="etat_fantome")
    codes = _codes(verifier(regles_fabriquees.charger()), "erreur")
    assert {"valeur_inconnue", "profil_inconnu", "choix_invalide", "verification_inconnue"} <= set(codes)


@pytest.mark.parametrize("champs, code", [
    ({"quand": {"sudoc": "inconnu"}}, "valeur_inconnue"),
    ({"quand": {"fait_fantome": "x"}}, "fait_inconnu"),
    ({"profil": "profil-fantome"}, "profil_inconnu"),
    ({"alors": "suppression"}, "action_inconnue"),
])
def test_defauts_de_decision(regles_fabriquees, champs, code):
    regles_fabriquees.modifier_decision("Thèse EPHE absente du Sudoc", **champs)
    assert code in _codes(verifier(regles_fabriquees.charger()), "erreur")


def test_identifiant_de_decision_en_double(regles_fabriquees):
    regles_fabriquees.ajouter_decision({"nom": "Thèse EPHE absente du Sudoc", "quand": {"type": "thèse"},
                                        "alors": "arrêt"})
    assert "decision_doublon" in _codes(verifier(regles_fabriquees.charger()), "erreur")


def test_avertissements_et_infos(regles_fabriquees):
    # variante atteinte par aucune décision ni vérification, vérification sans état
    regles_fabriquees.modifier_decision("Mémoire absent du Sudoc", profil="these-originale-imprimee")
    regles_fabriquees.modifier_decision("Mémoire, notice électronique au Sudoc", profil="these-originale-imprimee")
    e = regles_fabriquees.lire("verifications.yaml")
    del e["situation_non_prevue"]
    regles_fabriquees.ecrire("verifications.yaml", e)
    d = regles_fabriquees.lire("decisions.yaml")
    d["sinon"] = {"alors": "vérification", "motif": "Situation non prévue"}
    regles_fabriquees.ecrire("decisions.yaml", d)
    regles_fabriquees.modifier_decision("Résultat Sudoc à vérifier", verification=None)
    r = verifier(regles_fabriquees.charger())
    assert {"profil_non_atteint", "verification_sans_etat"} <= set(_codes(r, "avertissement"))
    assert "variable_inutilisee" in _codes(r, "info")
    assert not r.bloquant


def test_cas_non_couvert_comble(regles_fabriquees):
    regles_fabriquees.ajouter_decision({"nom": "Mémoire déjà au Sudoc", "quand": {"type": "mémoire"},
                                        "alors": "vérification", "verification": "sudoc_a_qualifier"})
    r = verifier(regles_fabriquees.charger())
    assert sum(c.code == "cas_non_couvert" for c in r.constats) == 1


# ---------------------------------------------------------------- brouillons (interface)
def test_verifier_un_brouillon_sans_ecrire(regles_fabriquees):
    texte = regles_fabriquees.texte("decisions.yaml").replace("quand: {alma: présent}", "quand: {alma: présente}")
    r = verifier_textes({"decisions.yaml": texte}, regles_fabriquees.reglages)
    assert r.bloquant and "valeur_inconnue" in _codes(r)
    assert regles_fabriquees.texte("decisions.yaml") != texte  # rien n'est écrit
    r = verifier_textes({"decisions.yaml": "regles: [\n"}, regles_fabriquees.reglages)
    assert _codes(r) == ["yaml_invalide"]


def test_regles_dir(regles_fabriquees):
    (regles_fabriquees.racine / "autres").mkdir()
    import shutil
    shutil.copytree(regles_fabriquees.dossier, regles_fabriquees.racine / "autres" / "regles")
    from humatheque.config import Reglages
    r = charger_regles(Reglages(_env_file=None, racine=regles_fabriquees.racine, regles_dir="autres/regles"))
    assert r.emplacement.endswith("autres/regles")
