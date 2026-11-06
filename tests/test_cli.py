"""`humatheque check-spec` (SPEC §8, §11 étape 1)."""

import json

from humatheque.cli import main


def test_check_spec_sur_spec_actuelle(capsys):
    assert main(["check-spec", "--source", "csv"]) == 0
    sortie = capsys.readouterr().out
    assert "0 erreur(s) bloquante(s)" in sortie


def test_check_spec_code_retour_si_erreur(regles_fabriquees, capsys):
    regles_fabriquees.modifier_ligne("these-originale-imprimee", "R005 R006", ligne="101 0#$a{langue_inconnue}")
    assert main(["check-spec", "--source", "csv", "--dossier", str(regles_fabriquees.dossier)]) == 1
    sortie = capsys.readouterr().out
    assert "R005" in sortie and "variable inconnue" in sortie


def test_check_spec_json(capsys):
    assert main(["check-spec", "--source", "csv", "--json"]) == 0
    rapport = json.loads(capsys.readouterr().out)
    assert rapport["source"] == "csv" and "constats" in rapport


def test_check_spec_yaml_invalide(regles_fabriquees, capsys):
    regles_fabriquees.ecrire("variables.yaml", "titre: {source: [\n")
    assert main(["check-spec", "--source", "csv", "--dossier", str(regles_fabriquees.dossier)]) == 1
    sortie = capsys.readouterr().out
    assert "variables.yaml" in sortie and "YAML invalide" in sortie
