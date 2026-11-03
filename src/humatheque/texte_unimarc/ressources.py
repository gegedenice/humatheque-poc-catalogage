"""Accès paresseux au dictionnaire reaccentue et à `config/texte_unimarc.yaml`."""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from humatheque.config import charger_yaml, reglages
from humatheque.texte_unimarc._vendor.reaccentue.dictionnaire import Dictionnaire, charger

NOM_CACHE = "reaccentue-cache.pz"


@lru_cache
def dictionnaire() -> Dictionnaire:
    """Dictionnaire de reaccentue ; cache dans `data/` (construit au premier appel)."""
    return charger(reglages().dossier_data / NOM_CACHE)


@lru_cache
def configuration() -> dict[str, Any]:
    return charger_yaml("texte_unimarc")
