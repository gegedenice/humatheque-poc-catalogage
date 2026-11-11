"""Accès à `config/texte_unimarc.yaml` (civilités, particules, articles)."""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from humatheque.config import charger_yaml


@lru_cache
def configuration() -> dict[str, Any]:
    return charger_yaml("texte_unimarc")
