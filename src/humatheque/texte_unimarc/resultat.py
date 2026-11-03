"""Types de retour des fonctions de normalisation (SPEC §4)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class Resultat(BaseModel):
    """Valeur normalisée, alertes (`code:détail`) et trace des décisions."""

    valeur: str
    alertes: list[str] = Field(default_factory=list)
    trace: list[str] = Field(default_factory=list)


class Personne(BaseModel):
    """Nom de personne découpé (SPEC §4.3)."""

    forme: str
    nom: str
    prenom: str
    alertes: list[str] = Field(default_factory=list)
    trace: list[str] = Field(default_factory=list)
