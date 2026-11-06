"""Réglages (SPEC §10)."""

from humatheque.config import Reglages, charger_yaml


def test_statuts_exclus_liste_separee_par_virgules(monkeypatch):
    monkeypatch.setenv("REGLES_STATUTS_EXCLUS", "rejeté, obsolète")
    assert Reglages(_env_file=None).regles_statuts_exclus == ["rejeté", "obsolète"]


def test_valeurs_par_defaut_sans_env(monkeypatch):
    for v in ("REGLES_DIR", "REGLES_STATUTS_EXCLUS", "CODE_ETAB"):
        monkeypatch.delenv(v, raising=False)
    r = Reglages(_env_file=None)
    assert r.regles_statuts_exclus == ["rejeté"]
    assert r.dossier_regles.name == "regles"


def test_secrets_masques(monkeypatch):
    monkeypatch.setenv("ALMA_API_KEY", "cle-secrete")
    monkeypatch.setenv("SPEC_SOURCE", "grist")  # ancienne variable : ignorée
    r = Reglages(_env_file=None)
    assert "cle-secrete" not in repr(r)
    assert r.alma_api_key.get_secret_value() == "cle-secrete"


def test_yaml_de_configuration_present():
    assert charger_yaml("etats_sudoc")["correspondance"]["printed_original"] == "originale imprimée"


def test_seuils_vides_none(monkeypatch):
    monkeypatch.setenv("DEWEY_SEUIL", "")
    r = Reglages(_env_file=None)
    assert r.dewey_seuil is None
