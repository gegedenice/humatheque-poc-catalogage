"""Provenance d'une thèse, déduite de `granting_institution` (SPEC §6, étape 2).

Fonction pure ; règles et formes dans `config/provenance.yaml`. Ordre :
1. établissement vide → `absent` ;
2. une forme EPHE dans l'établissement → `EPHE` ;
3. EPHE seulement en cotutelle → `ambigu` ;
4. établissement connu distinct de l'EPHE → `hors EPHE` ;
5. indice partiel (« hautes études » sans « pratique »…) → `ambigu` ;
6. sinon → `hors EPHE`.
Un mémoire a la provenance `mémoire`.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from functools import lru_cache
from typing import Any

from pydantic import BaseModel, Field

from humatheque.config import charger_yaml


class Provenance(BaseModel):
    valeur: str
    regle: str = Field(description="Règle appliquée (journalisée)")
    forme_comparee: str = Field(description="Forme normalisée comparée")
    forme_reconnue: str | None = None
    trace: list[str] = Field(default_factory=list)

    @property
    def a_confirmer(self) -> bool:
        """`absent` et `ambigu` arrêtent le document (routage décision)."""
        return self.valeur in {_cfg()["valeurs"]["absent"], _cfg()["valeurs"]["ambigu"]}


@lru_cache
def _cfg() -> dict[str, Any]:
    return charger_yaml("provenance")


def normaliser(texte: str | None) -> str:
    """Minuscules, sans accents, points retirés, autre ponctuation → espace."""
    t = unicodedata.normalize("NFKD", texte or "")
    t = "".join(c for c in t if not unicodedata.combining(c)).lower()
    t = t.replace(".", "")
    t = re.sub(r"[^\w\s]|_", " ", t)
    return " ".join(t.split())


def _contient(texte: str, forme: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(forme)}(?!\w)", texte) is not None


def _premiere(texte: str, formes: Iterable[str]) -> str | None:
    return next((f for f in formes if _contient(texte, normaliser(f))), None)


def provenance(
    granting_institution: str | None,
    co_tutelle_institutions: Iterable[str] | None = (),
    type_document: str = "these",
) -> Provenance:
    cfg = _cfg()
    v = cfg["valeurs"]
    forme = normaliser(granting_institution)
    if type_document == "memoire":
        return Provenance(valeur=v["memoire"], regle="memoire", forme_comparee=forme)
    if not forme:
        return Provenance(valeur=v["absent"], regle="etablissement_vide", forme_comparee="")
    if f := _premiere(forme, cfg["formes_ephe"]):
        return Provenance(valeur=v["ephe"], regle="forme_ephe", forme_comparee=forme, forme_reconnue=f)
    for cot in co_tutelle_institutions or []:
        if f := _premiere(normaliser(cot), cfg["formes_ephe"]):
            return Provenance(valeur=v["ambigu"], regle="ephe_en_cotutelle_seulement",
                              forme_comparee=forme, forme_reconnue=f,
                              trace=[f"cotutelle « {cot} »"])
    if f := _premiere(forme, cfg["formes_hors_ephe"]):
        return Provenance(valeur=v["hors_ephe"], regle="forme_hors_ephe", forme_comparee=forme, forme_reconnue=f)
    if f := _premiere(forme, cfg["indices_partiels"]):
        return Provenance(valeur=v["ambigu"], regle="correspondance_partielle", forme_comparee=forme, forme_reconnue=f)
    return Provenance(valeur=v["hors_ephe"], regle="aucune_forme_ephe", forme_comparee=forme)
