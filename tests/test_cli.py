"""`humatheque check-spec` (SPEC §8, §11 étape 1)."""

import json

from humatheque.cli import main


def test_check_spec_sur_spec_actuelle(capsys):
    assert main(["check-spec", "--source", "csv"]) == 0
    sortie = capsys.readouterr().out
    assert "0 erreur(s) bloquante(s)" in sortie


def test_check_spec_code_retour_si_erreur(spec_fabriquee, capsys):
    spec_fabriquee.modifier("Regles", "id", "R009", gabarit="105 ##$a")
    assert main(["check-spec", "--source", "csv", "--dossier", str(spec_fabriquee.dossier)]) == 1
    assert "R009" in capsys.readouterr().out


def test_check_spec_json(capsys):
    assert main(["check-spec", "--source", "csv", "--json"]) == 0
    rapport = json.loads(capsys.readouterr().out)
    assert rapport["source"] == "csv" and "constats" in rapport


def test_check_spec_table_manquante(spec_fabriquee, capsys):
    (spec_fabriquee.dossier / "Profils.csv").unlink()
    assert main(["check-spec", "--source", "csv", "--dossier", str(spec_fabriquee.dossier)]) == 2
    assert "Profils.csv" in capsys.readouterr().err
