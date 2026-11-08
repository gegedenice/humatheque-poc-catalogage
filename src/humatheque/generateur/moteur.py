"""Générateur (SPEC §8) : contexte du document + notice-modèle (socle + variante de `regles/notice/`) → notice.

Le générateur ne lit que le contexte : `variables_profil` en tire les variables
de `regles/variables.yaml`, puis `regles.notice.generer` remplit les zones.
Aucune zone ni valeur UNIMARC ici.
"""

from __future__ import annotations

from typing import Any

from humatheque.contexte import variables_profil
from humatheque.generateur.modeles import Notice
from humatheque.regles.modeles import Regles
from humatheque.regles.notice import GenerationEchec, generer as remplir

__all__ = ["GenerationEchec", "Generateur", "generer"]


class Generateur:
    def __init__(self, regles: Regles) -> None:
        self.regles = regles

    def generer(self, contexte: dict[str, Any], profil: str) -> Notice:
        return remplir(self.regles, profil, variables_profil(contexte, self.regles))


def generer(regles: Regles, contexte: dict[str, Any], profil: str) -> Notice:
    return Generateur(regles).generer(contexte, profil)
