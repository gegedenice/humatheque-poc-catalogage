"""Réglages (SPEC §10)."""

from humatheque.config import Reglages, charger_yaml


def test_valeurs_par_defaut_sans_env(monkeypatch):
    for v in ("REGLES_DIR", "CODE_ETAB"):
        monkeypatch.delenv(v, raising=False)
    r = Reglages(_env_file=None)
    assert r.dossier_regles.name == "regles"


def test_secrets_masques(monkeypatch):
    monkeypatch.setenv("ALMA_API_KEY", "cle-secrete")
    monkeypatch.setenv("SPEC_SOURCE", "grist")  # ancienne variable : ignorée
    r = Reglages(_env_file=None)
    assert "cle-secrete" not in repr(r)
    assert r.alma_api_key.get_secret_value() == "cle-secrete"


def test_yaml_de_configuration_present():
    assert charger_yaml("services")["alma"]["routes"]["these"] == "/check/thesis"


def test_seuils_vides_none(monkeypatch):
    monkeypatch.setenv("DEWEY_SEUIL", "")
    r = Reglages(_env_file=None)
    assert r.dewey_seuil is None
