"""Décision (SPEC §7) : le pipeline de `regles/pipeline.yaml` appliqué au dossier.

- `valeurs_etape` : ce que lit une étape — type, ephe, et les codes renvoyés
  par l'API de l'étape (status, thesis_state), sans traduction ;
- `evaluer` (regles.moteur) donne le résultat : stop, stop à reprendre,
  conversion, poursuite (avec alerte, à reprendre), création ;
- `Decision` : ce qui a arrêté le traitement ou choisi la notice produite ;
- `routage_final` : l'issue la plus sévère des étapes (`humatheque.issues`).
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from humatheque import issues
from humatheque.regles.moteur import Issue

Suite = Literal["creer", "convertir", "arreter"]

# Provenance (decision/provenance.py) → variable « ephe » du pipeline.
EPHE = {"EPHE": "oui", "hors EPHE": "non", "absent": "absent", "ambigu": "ambigu"}


class Decision(BaseModel):
    """Résultat d'étape qui arrête le traitement ou fixe la notice produite."""

    model_config = ConfigDict(extra="ignore")

    etape: str = ""
    faits: dict[str, Any] = Field(default_factory=dict, description="valeurs lues par l'étape")
    chemin: list[str] = Field(default_factory=list, description="branches traversées dans l'arbre")
    resultat: str = ""
    situation: str | None = Field(None, description="résumé lisible : étape › chemin")
    suite: Suite
    profil_cible: str | None = None
    routage: str
    motif: str

    @model_validator(mode="before")
    @classmethod
    def _ancienne(cls, v: Any) -> Any:
        """Dossiers enregistrés avant le 09/11 (règles nommées) : relus tels quels."""
        if isinstance(v, dict) and "resultat" not in v and v.get("action"):
            v = {**v, "resultat": {"création": "création", "conversion": "conversion", "arrêt": "stop"}.get(
                v["action"], "stop à reprendre")}
        return v

    def texte_faits(self) -> str:
        return ", ".join(f"{k} = {v}" for k, v in self.faits.items() if v is not None)

    @classmethod
    def depuis(cls, issue: Issue, libelle: str) -> "Decision":
        suite: Suite = {"conversion": "convertir", "création": "creer"}.get(issue.resultat, "arreter")  # type: ignore[assignment]
        motif = issue.motif or issue.texte
        return cls(etape=issue.etape, faits=issue.valeurs, chemin=issue.chemin, resultat=issue.resultat,
                   situation=f"{libelle} › {' › '.join(issue.chemin)}", suite=suite,
                   profil_cible=issue.notice if suite != "arreter" else None, routage=issue.issue,
                   motif=f"{libelle} : {motif}")


def ephe(provenance: str | None) -> str | None:
    return EPHE.get(provenance or "")


def valeurs_etape(type_document: str, provenance: str | None, **codes: Any) -> dict[str, Any]:
    """Valeurs lues par une étape : type, ephe, codes de l'API (status, thesis_state…)."""
    return {"type": type_document, "ephe": ephe(provenance), **codes}


def routage_final(routages: Iterable[str | None]) -> str:
    """Issue du dossier : la plus sévère de ses étapes."""
    return issues.la_plus_severe(routages)
