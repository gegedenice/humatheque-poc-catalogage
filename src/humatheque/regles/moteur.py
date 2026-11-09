"""Moteur du pipeline (SPEC §7) : pour une étape, valeurs lues (codes renvoyés
par l'API, type, ephe) → résultat de l'arbre de `regles/pipeline.yaml`.

Parcours dans l'ordre de lecture de l'arbre : la première feuille qui
correspond l'emporte. Une branche avec des sous-branches descend ; si aucune
ne correspond, son `sinon` s'il existe, sinon la lecture continue aux branches
suivantes ; à la fin, le `sinon` de l'étape. Le chemin parcouru est gardé.
Aucune règle ici : seulement l'interprétation des nœuds.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from pydantic import BaseModel, Field

from humatheque import issues
from humatheque.config import charger_yaml
from humatheque.regles.modeles import EtapePipeline, Noeud, Regles


@lru_cache
def conventions() -> dict[str, Any]:
    return charger_yaml("regles")


class Issue(BaseModel):
    """Résultat d'une étape : ce qui arrive au document, et pourquoi."""

    etape: str
    valeurs: dict[str, Any] = Field(default_factory=dict)
    chemin: list[str] = Field(default_factory=list, description="conditions des nœuds traversés")
    resultat: str
    notice: str | None = None
    motif: str | None = None
    alerte: str | None = None
    sinon: bool = Field(False, description="aucune branche ne correspond")

    @property
    def arret(self) -> bool:
        return self.resultat in ("stop", "stop à reprendre", "conversion")

    @property
    def issue(self) -> str:
        """Issue du document (terminé, à reprendre) qui découle du résultat."""
        return issues.A_REPRENDRE if "reprendre" in self.resultat else issues.TERMINE

    @property
    def texte(self) -> str:
        n = Noeud(resultat=self.resultat, notice=self.notice)
        return n.texte_resultat

    @property
    def explication(self) -> str:
        chemin = " → ".join(self.chemin) if self.chemin else "aucune branche"
        return f"{chemin} : {self.texte}" + (f" ({self.motif})" if self.motif else "")


def _parcourir(noeuds: list[Noeud], valeurs: dict[str, Any], chemin: list[str]) -> tuple[Noeud, list[str]] | None:
    for n in noeuds:
        if not n.correspond(valeurs):
            continue
        ici = [*chemin, n.condition]
        if not n.branches:
            return n, ici
        trouve = _parcourir(n.branches, valeurs, ici)
        if trouve:
            return trouve
        if n.sinon is not None:
            return n.sinon, [*ici, "sinon"]
    return None


def evaluer(regles: Regles, etape: str, valeurs: dict[str, Any]) -> Issue:
    """Résultat de l'étape `etape` pour ces valeurs."""
    e: EtapePipeline | None = regles.etapes.get(etape)
    if e is None:  # étape non configurée : on poursuit
        return Issue(etape=etape, valeurs=valeurs, resultat="poursuite", motif="étape non configurée")
    trouve = _parcourir(e.branches, valeurs, [])
    if trouve is None:
        n, chemin, sinon = e.sinon, ["sinon"], True
    else:
        (n, chemin), sinon = trouve, chemin_sinon(trouve[1])
    return Issue(etape=etape, valeurs=valeurs, chemin=chemin, resultat=n.resultat or "stop à reprendre",
                 notice=n.notice, motif=n.motif, alerte=n.alerte, sinon=sinon)


def chemin_sinon(chemin: list[str]) -> bool:
    return bool(chemin) and chemin[-1] == "sinon"


def valeurs_possibles(regles: Regles, etape: str) -> dict[str, list[str]]:
    """Variables lisibles à une étape : communes + celles de l'étape."""
    e = regles.etapes.get(etape)
    return {**regles.communes, **(e.variables if e else {})}


def valeurs_utilisees(noeuds: list[Noeud]) -> set[str]:
    sortie: set[str] = set()
    for n in noeuds:
        sortie |= set(n.si) | set(n.sauf) | valeurs_utilisees(n.branches)
        if n.sinon:
            sortie |= valeurs_utilisees([n.sinon])
    return sortie
