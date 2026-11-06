"""Modèles des règles de catalogage (SPEC §8) : variables, profils, décisions,
et la table `Statuts_services` (restée en CSV ou Grist, §7).

Les fichiers YAML sont validés à la lecture ; les conditions rule-engine sont
compilées par le moteur (`regles/moteur.py`).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Variable(BaseModel):
    model_config = ConfigDict(extra="allow")

    nom: str
    source: str = ""
    type: Literal["texte", "personne", "organisme"] = "texte"
    liste: bool = False
    disponible: bool = True
    description: str = ""


class Ligne(BaseModel):
    """Une ligne de profil : une zone écrite comme dans WinIBW (`ligne || repli`)."""

    model_config = ConfigDict(extra="allow")

    ligne: str
    si: str | None = None
    priorite: int | None = None
    statut: str | None = None
    remarque: str | None = None
    ref: str | None = None
    actif: bool = True
    joindre: dict[str, list[str]] = Field(default_factory=dict,
                                          description="variable liste → [séparateur, dernier séparateur]")
    numero: int = Field(0, description="rang de la ligne dans le profil (1, 2…)")

    @field_validator("ligne", "ref", "statut", "remarque", "si", mode="before")
    @classmethod
    def _texte(cls, v: Any) -> Any:
        return None if v is None else str(v)

    @field_validator("joindre", mode="before")
    @classmethod
    def _joindre(cls, v: Any) -> Any:
        if v is None:
            return {}
        if isinstance(v, dict):
            return {k: ([s] if isinstance(s, str) else list(s)) for k, s in v.items()}
        return v

    @property
    def principal(self) -> str:
        return self.ligne.partition("||")[0].strip()

    @property
    def repli(self) -> str:
        return self.ligne.partition("||")[2].strip()

    @property
    def etiquette(self) -> str:
        return self.ligne.strip()[:3]

    @property
    def trace(self) -> str:
        return self.ref or f"ligne {self.numero}"


class Profil(BaseModel):
    model_config = ConfigDict(extra="allow")

    profil: str
    libelle: str = ""
    variables: list[str] = Field(default_factory=list)
    lignes: list[Ligne]
    fichier: str = ""


class RegleDecision(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    si: str
    alors: str
    profil: str | None = None
    motif: str | None = None
    routage: str | None = None
    statut: str | None = None


class StatutService(BaseModel):
    """Ligne de Statuts_services (CSV ou Grist) ; cellules texte."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    service: str
    statut: str
    signification: str = ""
    routage: str = ""
    remarque: str = ""

    @field_validator("*", mode="before")
    @classmethod
    def _texte(cls, v: object) -> object:
        return "" if v is None else (v.strip() if isinstance(v, str) else v)

    @property
    def id(self) -> str:
        return f"{self.service} / {self.statut}"


class Regles(BaseModel):
    """Règles chargées, avec leur provenance et leur version."""

    variables: dict[str, Variable]
    profils: dict[str, Profil]
    faits: dict[str, list[str]]
    decisions: list[RegleDecision]
    sinon: dict[str, Any]
    statuts_services: list[StatutService]

    source: Literal["csv", "grist"] = Field("csv", description="source de Statuts_services")
    emplacement: str = Field(description="dossier des règles")
    emplacement_statuts: str = ""
    horodatage: datetime
    empreinte: str = Field(description="SHA-256 (12 caractères) des fichiers de règles et de Statuts_services")
    avertissements: list[str] = Field(default_factory=list)
    textes: dict[str, str] = Field(default_factory=dict, description="chemin relatif → texte YAML lu")

    @property
    def version(self) -> str:
        return f"{self.horodatage:%Y-%m-%dT%H:%M:%SZ} {self.empreinte}"
