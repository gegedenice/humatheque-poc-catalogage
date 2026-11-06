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

    def ajouter_ligne(self, profil: str, ligne: str | dict) -> None:
        p = self.lire(f"profils/{profil}.yaml")
        p["lignes"].append(ligne)
        self.ecrire(f"profils/{profil}.yaml", p)

    def modifier_ligne(self, profil: str, debut: str, **champs: Any) -> None:
        """Modifie les lignes du profil qui commencent par `debut` (ex. « 101 0# »)."""
        p = self.lire(f"profils/{profil}.yaml")
        p["lignes"] = [l if isinstance(l, dict) else {"ligne": l} for l in p["lignes"]]
        cibles = [l for l in p["lignes"] if l["ligne"].startswith(debut)]
        assert cibles, f"{profil} : aucune ligne « {debut} »"
        for l in cibles:
            l.update(champs)
        self.ecrire(f"profils/{profil}.yaml", p)

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
