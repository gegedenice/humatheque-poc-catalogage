"""Client dewey-classifier (`/classify`), SPEC §6 étape 7.

Texte = titre + sous-titre + discipline (POC `build_dewey_text`) ; la
première classe donne `dewey_code`. Statut : `aucune_classe`, sinon
`au_dessus_seuil` ou `sous_seuil` selon `DEWEY_SEUIL` (seuil non fixé : la première classe est
retenue).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from humatheque.config import charger_yaml
from humatheque.services.base import Appel, ClientService, texte


class ClasseDewey(BaseModel):
    code: str | None
    libelle: str | None = None
    score: float | None = None


class ResultatDewey(BaseModel):
    statut: str
    code: str | None = None
    libelle: str | None = None
    score: float | None = None
    classes: list[ClasseDewey] = Field(default_factory=list)


def texte_dewey(m: dict[str, Any]) -> str:
    return " ".join(p for p in (texte(m.get("title")), texte(m.get("subtitle")), texte(m.get("discipline"))) if p)


def classer(client: ClientService, m: dict[str, Any], seuil: float | None,
            parametres: dict[str, Any] | None = None) -> tuple[ResultatDewey, Appel]:
    cfg = charger_yaml("services")["dewey"]
    reponse, appel = client.post(cfg["route"], {"text": texte_dewey(m), **cfg["parametres"], **(parametres or {})})
    brutes = ((reponse.get("results") or [{}])[0] or {}).get("classes") or []
    classes = [ClasseDewey(code=c.get("dewey"), libelle=c.get("label"), score=c.get("score")) for c in brutes]
    if not classes or not classes[0].code:
        return ResultatDewey(statut="aucune_classe", classes=classes), appel
    premiere = classes[0]
    if seuil is not None and (premiere.score or 0) < seuil:
        return ResultatDewey(statut="sous_seuil", classes=classes), appel
    return ResultatDewey(statut="au_dessus_seuil", code=premiere.code, libelle=premiere.libelle,
                         score=premiere.score, classes=classes), appel
