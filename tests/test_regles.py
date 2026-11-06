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
    assert regles.decisions[0].id == "S01" and regles.sinon["alors"] == "décision"
    assert regles.variables["auteur"].type == "personne" and regles.variables["directeur"].liste
    assert not regles.variables["numero_nnt"].disponible
    assert len(regles.empreinte) == 12 and regles.version.endswith(regles.empreinte)
    assert regles.statuts_services and regles.source == "csv"
    ligne = regles.profils["these-originale-imprimee"].lignes[0]
    assert (ligne.numero, ligne.etiquette, ligne.trace) == (1, "008", "R001")


def test_aucune_erreur_et_quatre_cas_non_couverts(regles):
    r = verifier(regles)
    assert not r.erreurs
    trous = [c.message for c in r.constats if c.code == "cas_non_couvert"]
    assert len(trous) == 4
    assert any("type = thèse, provenance = EPHE, sudoc = originale imprimée" in t for t in trous)
    assert sum("type = mémoire" in t for t in trous) == 3
    assert _codes(r, "avertissement").count("statut_ignore") == 2


def test_empreinte_stable_et_sensible(regles_fabriquees):
    a, b = regles_fabriquees.charger(), regles_fabriquees.charger()
    assert a.empreinte == b.empreinte
    regles_fabriquees.modifier_decision("S08", motif="autre motif")
    assert regles_fabriquees.charger().empreinte != a.empreinte


# ---------------------------------------------------------------- défauts fabriqués (§8.4)
def test_yaml_invalide_avec_ligne(regles_fabriquees):
    regles_fabriquees.ecrire("decisions.yaml", regles_fabriquees.texte("decisions.yaml") + "\n  - {id: X, si: [\n")
    with pytest.raises(ReglesInvalides) as e:
        regles_fabriquees.charger()
    (fichier, ligne, message), = e.value.problemes
    assert fichier == "decisions.yaml" and ligne.startswith("ligne ") and "YAML invalide" in message


def test_structure_absente(regles_fabriquees):
    regles_fabriquees.ecrire("profils/memoire-original-imprime.yaml", "profil: x\n")
    with pytest.raises(ReglesInvalides, match="clés profil et lignes attendues"):
        regles_fabriquees.charger()


def test_sinon_obligatoire(regles_fabriquees):
    d = regles_fabriquees.lire("decisions.yaml")
    del d["sinon"]
    regles_fabriquees.ecrire("decisions.yaml", d)
    with pytest.raises(ReglesInvalides, match="sinon"):
        regles_fabriquees.charger()


@pytest.mark.parametrize("ligne, code", [
    ("10 ##$aX", "zone_illisible"),
    ("330 ##$a{inexistante}", "variable_inconnue"),
    ("330 ##$a{titre.ppn}", "attribut_impossible"),
    ("701 #1$3{directeur.ppn}$3{rapporteur.ppn}", "repetition_multiple"),
    ({"ligne": "330 ##$a{titre}", "joindre": {"titre": ", "}}, "joindre_invalide"),
    ({"ligne": "330 ##$a{titre}", "si": "titre ==="}, "condition_invalide"),
])
def test_defauts_de_profil(regles_fabriquees, ligne, code):
    regles_fabriquees.ajouter_ligne("these-originale-imprimee", ligne)
    r = verifier(regles_fabriquees.charger())
    assert code in _codes(r, "erreur")


@pytest.mark.parametrize("champs, code", [
    ({"si": 'sudoc == "rien" and'}, "condition_invalide"),
    ({"profil": "profil-fantome"}, "profil_inconnu"),
    ({"alors": "suppression"}, "action_inconnue"),
    ({"routage": "peut-être"}, "routage_inconnu"),
])
def test_defauts_de_decision(regles_fabriquees, champs, code):
    regles_fabriquees.modifier_decision("S08", **champs)
    assert code in _codes(verifier(regles_fabriquees.charger()), "erreur")


def test_identifiant_de_decision_en_double(regles_fabriquees):
    regles_fabriquees.ajouter_decision({"id": "S08", "si": "false", "alors": "décision", "motif": "x"})
    assert "decision_doublon" in _codes(verifier(regles_fabriquees.charger()), "erreur")


def test_avertissements_et_infos(regles_fabriquees):
    # variable utilisée sans être déclarée, déclarée sans être utilisée, profil non atteint
    p = regles_fabriquees.lire("profils/memoire-original-imprime.yaml")
    p["variables"] = [v for v in p["variables"] if v != "langue"] + ["dewey"]
    regles_fabriquees.ecrire("profils/memoire-original-imprime.yaml", p)
    regles_fabriquees.modifier_decision("S12", profil="these-originale-imprimee")
    regles_fabriquees.modifier_decision("S13", profil="these-originale-imprimee")
    r = verifier(regles_fabriquees.charger())
    assert {"variable_non_declaree", "profil_non_atteint"} <= set(_codes(r, "avertissement"))
    assert "variable_inutilisee" in _codes(r, "info")
    assert not r.bloquant


def test_cas_non_couvert_comble(regles_fabriquees):
    regles_fabriquees.ajouter_decision({"id": "S15", "si": 'type == "mémoire"', "alors": "décision",
                                        "motif": "mémoire déjà signalé au Sudoc"})
    r = verifier(regles_fabriquees.charger())
    assert sum(c.code == "cas_non_couvert" for c in r.constats) == 1


def test_statuts_services_introuvable(regles_fabriquees):
    (regles_fabriquees.racine / "spec" / "Statuts_services.csv").unlink()
    with pytest.raises(ReglesInvalides, match="Statuts_services"):
        regles_fabriquees.charger()


# ---------------------------------------------------------------- brouillons (interface)
def test_verifier_un_brouillon_sans_ecrire(regles_fabriquees):
    texte = regles_fabriquees.texte("decisions.yaml").replace('alma == "présent"', 'alma == ')
    r = verifier_textes({"decisions.yaml": texte}, regles_fabriquees.reglages)
    assert r.bloquant and "condition_invalide" in _codes(r)
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
