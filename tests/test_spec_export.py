"""humatheque spec-export : instantané de Statuts_services (Grist) dans spec/ (SPEC §8.1)."""

import csv

import httpx
import pytest
import respx

from humatheque.regles.export import exporter
from humatheque.regles.grist import GristInjoignable
from test_grist import _reglages, _simuler_grist


@respx.mock
def test_export_statuts_services(tmp_path):
    _simuler_grist(respx)
    r = exporter(_reglages(), tmp_path)
    assert r["tables"] == {"Statuts_services": 1} and len(r["empreinte"]) == 12
    with (tmp_path / "Statuts_services.csv").open(encoding="utf-8") as f:
        lignes = list(csv.DictReader(f))
    assert lignes == [{"service": "sudoc-checker", "statut": "error", "routage": "incident"}]
    assert (tmp_path / "archives" / r["horodatage"] / "Statuts_services.csv").exists()
    assert not (tmp_path / "Regles.csv").exists()  # les règles sont dans regles/ (YAML)


@respx.mock
def test_export_grist_injoignable(tmp_path):
    respx.get(url__startswith="http://grist.test").mock(side_effect=httpx.ConnectError("refusé"))
    with pytest.raises(GristInjoignable):
        exporter(_reglages(), tmp_path)
    assert not list(tmp_path.iterdir())  # rien écrit
