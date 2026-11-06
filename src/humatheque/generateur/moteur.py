"""Générateur (SPEC §8) : contexte du document + profil de `regles/profils/` → notice.

Le générateur ne lit que le contexte : `variables_profil` en tire les variables
de `regles/variables.yaml`, puis `regles.moteur.generer` remplit le profil.
Aucune zone ni valeur UNIMARC ici.
"""

from __future__ import annotations

from typing import Any

from humatheque.config import reglages
from humatheque.contexte import variables_profil
from humatheque.generateur.modeles import Notice
from humatheque.regles.modeles import Regles
from humatheque.regles.moteur import GenerationEchec, generer as remplir

__all__ = ["GenerationEchec", "Generateur", "generer"]


class Generateur:
    def __init__(self, regles: Regles, statuts_exclus: list[str] | None = None) -> None:
        self.regles = regles
        self.exclus = statuts_exclus if statuts_exclus is not None else reglages().regles_statuts_exclus

    def generer(self, contexte: dict[str, Any], profil: str) -> Notice:
        return remplir(self.regles, profil, variables_profil(contexte, self.regles), self.exclus)


def generer(regles: Regles, contexte: dict[str, Any], profil: str) -> Notice:
    return Generateur(regles).generer(contexte, profil)
