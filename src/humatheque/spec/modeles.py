"""Modèles de la spécification : une classe par table Grist (SPEC §8).

Les cellules sont conservées telles qu'exportées (chaînes) ; les propriétés
donnent les vues découpées (listes de profils, de sources, alternatives).
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


def decouper(cellule: str, separateur: str) -> list[str]:
    """Découpe une cellule multi-valeurs ; les éléments vides sont ignorés."""
    return [x.strip() for x in (cellule or "").split(separateur) if x.strip()]


class _Ligne(BaseModel):
    """Ligne de table : colonnes inconnues ignorées, valeurs nulles → chaîne vide."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    @field_validator("*", mode="before")
    @classmethod
    def _texte(cls, v: object) -> object:
        if v is None:
            return ""
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            # Grist renvoie des nombres pour les colonnes numériques (priorite).
            return str(int(v)) if float(v).is_integer() else str(v)
        if isinstance(v, str):
            return v.strip()
        return v


class Regle(_Ligne):
    id: str
    zone: str = ""
    profils: str = ""
    priorite: str = ""
    mode_production: str = ""
    sources: str = ""
    gabarit: str = ""
    condition: str = ""
    repli: str = ""
    statut: str = ""
    vigilance: str = ""

    def liste_profils(self, sep: str = ",") -> list[str]:
        return decouper(self.profils, sep)

    def liste_sources(self, sep: str = ",") -> list[str]:
        return decouper(self.sources, sep)


class Source(_Ligne):
    id: str
    type_source: str = ""
    etape: str = ""
    description: str = ""
    cardinalite: str = ""
    disponible: str = ""


class Situation(_Ligne):
    id: str
    etat_alma: str = ""
    etat_sudoc: str = ""
    provenance: str = ""
    action: str = ""
    profil_cible: str = ""
    routage: str = ""
    statut: str = ""


class StatutService(_Ligne):
    service: str
    statut: str
    signification: str = ""
    routage: str = ""
    remarque: str = ""

    @property
    def id(self) -> str:
        return f"{self.service} / {self.statut}"


class Profil(_Ligne):
    code: str
    type_document: str = ""
    nature: str = ""
    description: str = ""
    nnt: str = ""
    statut: str = ""

    @property
    def id(self) -> str:
        return self.code


class Spec(BaseModel):
    """Spécification chargée, avec sa provenance et sa version."""

    regles: list[Regle]
    sources: list[Source]
    situations: list[Situation]
    statuts_services: list[StatutService]
    profils: list[Profil]

    source: Literal["csv", "grist"]
    emplacement: str = Field(description="Dossier CSV ou URL du document Grist")
    horodatage: datetime
    empreinte: str = Field(description="SHA-256 (12 caractères) du contenu des tables")
    avertissements: list[str] = Field(default_factory=list)

    @property
    def version(self) -> str:
        return f"{self.horodatage:%Y-%m-%dT%H:%M:%SZ} {self.empreinte}"
