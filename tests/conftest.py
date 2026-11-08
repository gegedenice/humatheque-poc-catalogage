"""Fixtures communes : copie de regles/ dans une racine temporaire,
modifiable fichier par fichier (règles fabriquées pour le vérificateur)."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import pytest
import yaml

RACINE = Path(__file__).resolve().parents[1]


class ReglesFabriquees:
    """Copie de regles/ que l'on altère pour fabriquer des défauts."""

    def __init__(self, racine: Path) -> None:
        self.racine = racine
        self.dossier = racine / "regles"

    @property
    def reglages(self):
        from humatheque.config import Reglages

        return Reglages(_env_file=None, racine=self.racine)

    def texte(self, rel: str) -> str:
        return (self.dossier / rel).read_text(encoding="utf-8")

    def ecrire(self, rel: str, contenu: str | dict) -> None:
        if not isinstance(contenu, str):
            contenu = yaml.safe_dump(contenu, allow_unicode=True, sort_keys=False)
        (self.dossier / rel).write_text(contenu, encoding="utf-8")

    def lire(self, rel: str) -> Any:
        return yaml.safe_load(self.texte(rel))

    def ajouter_zone(self, ou: str, zone: dict) -> None:
        """Ajoute une zone au socle (`ou` = « socle ») ou à une variante (`ajouter`)."""
        if ou == "socle":
            p = self.lire("notice/socle.yaml")
            p["zones"].append(zone)
            self.ecrire("notice/socle.yaml", p)
        else:
            p = self.lire(f"notice/variantes/{ou}.yaml")
            p.setdefault("ajouter", []).append(zone)
            self.ecrire(f"notice/variantes/{ou}.yaml", p)

    def modifier_zone(self, id_: str, **champs: Any) -> None:
        """Modifie une zone du socle."""
        p = self.lire("notice/socle.yaml")
        cibles = [z for z in p["zones"] if str(z["id"]) == id_]
        assert cibles, f"aucune zone « {id_} » dans le socle"
        for z in cibles:
            z.update(champs)
        self.ecrire("notice/socle.yaml", p)

    def modifier_sous_zone(self, id_: str, code: str, **champs: Any) -> None:
        p = self.lire("notice/socle.yaml")
        (z,) = [z for z in p["zones"] if str(z["id"]) == id_]
        cibles = [sz for sz in z["sous_zones"] if str(sz["code"]) == code]
        assert cibles, f"zone {id_} : aucune sous-zone ${code}"
        for sz in cibles:
            sz.update(champs)
        self.ecrire("notice/socle.yaml", p)

    def modifier_decision(self, id_: str, **champs: Any) -> None:  # id_ : nom de la règle
        d = self.lire("decisions.yaml")
        cibles = [r for r in d["regles"] if r["nom"] == id_]
        assert cibles, f"aucune décision « {id_} »"
        for r in cibles:
            r.update(champs)
        self.ecrire("decisions.yaml", d)

    def ajouter_decision(self, regle: dict, position: int | None = None) -> None:
        d = self.lire("decisions.yaml")
        d["regles"].insert(len(d["regles"]) if position is None else position, regle)
        self.ecrire("decisions.yaml", d)

    def charger(self):
        from humatheque.regles import charger_regles

        return charger_regles(self.reglages)


@pytest.fixture
def regles_fabriquees(tmp_path: Path) -> ReglesFabriquees:
    shutil.copytree(RACINE / "regles", tmp_path / "regles", ignore=shutil.ignore_patterns("archives"))
    return ReglesFabriquees(tmp_path)


@pytest.fixture(scope="session")
def regles():
    from humatheque.regles import charger_regles

    return charger_regles()
