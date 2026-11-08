"""`humatheque check-spec` (SPEC §8, §11 étape 1)."""

import json

from humatheque.cli import main


def test_check_spec_sur_spec_actuelle(capsys):
    assert main(["check-spec"]) == 0
    sortie = capsys.readouterr().out
    assert "0 erreur(s) bloquante(s)" in sortie


def test_check_spec_code_retour_si_erreur(regles_fabriquees, capsys):
    regles_fabriquees.modifier_sous_zone("101", "a", variable="langue_inconnue")
    assert main(["check-spec", "--dossier", str(regles_fabriquees.dossier)]) == 1
    sortie = capsys.readouterr().out
    assert "zone 101" in sortie and "variable inconnue" in sortie


def test_check_spec_json(capsys):
    assert main(["check-spec", "--json"]) == 0
    rapport = json.loads(capsys.readouterr().out)
    assert rapport["version"] and "constats" in rapport


def test_check_spec_yaml_invalide(regles_fabriquees, capsys):
    regles_fabriquees.ecrire("variables.yaml", "titre: {source: [\n")
    assert main(["check-spec", "--dossier", str(regles_fabriquees.dossier)]) == 1
    sortie = capsys.readouterr().out
    assert "variables.yaml" in sortie and "YAML invalide" in sortie
