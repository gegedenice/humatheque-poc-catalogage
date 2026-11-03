"""Fixtures communes : copie de spec/ dans un dossier temporaire, modifiable cellule par cellule."""

from __future__ import annotations

import csv
import shutil
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parents[1]
SPEC = RACINE / "spec"


class SpecFabriquee:
    """Copie des CSV de spec/ que l'on altère pour fabriquer des défauts."""

    def __init__(self, dossier: Path) -> None:
        self.dossier = dossier

    def _lire(self, table: str) -> tuple[list[str], list[dict[str, str]]]:
        with (self.dossier / f"{table}.csv").open(encoding="utf-8", newline="") as f:
            r = csv.DictReader(f)
            return list(r.fieldnames or []), list(r)

    def _ecrire(self, table: str, colonnes: list[str], lignes: list[dict[str, str]]) -> None:
        with (self.dossier / f"{table}.csv").open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=colonnes, lineterminator="\n")
            w.writeheader()
            w.writerows(lignes)

    def modifier(self, table: str, cle: str, valeur_cle: str, **cellules: str) -> None:
        colonnes, lignes = self._lire(table)
        cibles = [ligne for ligne in lignes if ligne[cle] == valeur_cle]
        assert cibles, f"{table} : aucune ligne {cle}={valeur_cle}"
        for ligne in cibles:
            ligne.update(cellules)
        self._ecrire(table, colonnes, lignes)

    def ajouter(self, table: str, **cellules: str) -> None:
        colonnes, lignes = self._lire(table)
        lignes.append({c: cellules.get(c, "") for c in colonnes})
        self._ecrire(table, colonnes, lignes)

    def renommer_colonne(self, table: str, ancien: str, nouveau: str) -> None:
        colonnes, lignes = self._lire(table)
        self._ecrire(table, [nouveau if c == ancien else c for c in colonnes],
                     [{(nouveau if k == ancien else k): v for k, v in ligne.items()} for ligne in lignes])


@pytest.fixture
def spec_fabriquee(tmp_path: Path) -> SpecFabriquee:
    dossier = tmp_path / "spec"
    shutil.copytree(SPEC, dossier)
    return SpecFabriquee(dossier)
