"""humatheque spec-export : instantané Grist au format des exports CSV (SPEC §8)."""

import csv

import httpx
import pytest
import respx

from humatheque.spec import charger_spec
from humatheque.spec.export import exporter
from humatheque.spec.grist import GristInjoignable
from test_grist import TABLES, _reglages, _simuler_grist


@respx.mock
def test_export_aller_retour(tmp_path):
    _simuler_grist(respx)
    r = exporter(_reglages(), tmp_path)
    assert r["tables"] == {"Regles": 2, "Sources": 1, "Situations": 2, "Statuts_services": 1, "Profils": 2}
    with (tmp_path / "Regles.csv").open(encoding="utf-8") as f:
        lignes = list(csv.DictReader(f))
    assert list(lignes[0]) == ["id", "zone", "profils", "priorite", "mode_production", "sources", "gabarit",
                               "repli", "statut"]
    assert lignes[0]["profils"] == "these_originale_imprimee, these_reproduction_imprimee"
    assert lignes[0]["priorite"] == "1" and lignes[0]["repli"] == ""
    with (tmp_path / "Sources.csv").open(encoding="utf-8") as f:
        assert "cardinalité" in f.readline()
    assert (tmp_path / "archives" / r["horodatage"] / "Profils.csv").exists()
    # même contenu en CSV qu'en Grist : même empreinte
    depuis_grist = charger_spec(_reglages())
    depuis_csv = charger_spec(source="csv", dossier=tmp_path)
    assert depuis_csv.empreinte == depuis_grist.empreinte == r["empreinte"]


@respx.mock
def test_export_grist_injoignable(tmp_path):
    respx.get(url__startswith="http://grist.test").mock(side_effect=httpx.ConnectError("refusé"))
    with pytest.raises(GristInjoignable):
        exporter(_reglages(), tmp_path)
    assert not list(tmp_path.iterdir())  # rien écrit


def test_tables_simulees_completes():
    assert set(TABLES) == {"Regles", "Sources", "Situations", "Statuts_services", "Profils"}
