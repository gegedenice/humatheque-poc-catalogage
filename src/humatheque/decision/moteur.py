"""Moteur de décision (SPEC §7).

- `faits_du_dossier` : type, provenance, alma, sudoc (valeurs de `config/regles.yaml`) ;
- `decider` : faits → première règle de `regles/decisions.yaml` dont la condition
  rule-engine est vraie (sinon `sinon`) → suite du traitement (créer, convertir,
  arrêter) et issue (terminé, à reprendre) ;
- `routage_service` : `(service, statut)` → issue par `config/services.yaml`
  (`statuts`) ; alma et sudoc sont jugés par les états du §6 ;
- `routage_final` : l'issue la plus sévère des étapes (`humatheque.issues`).

Aucune décision ni aucune issue de service n'est écrite ici : tout vient de
`regles/` et de `config/*.yaml`.
"""

from __future__ import annotations

from collections.abc import Iterable
from functools import lru_cache
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from humatheque import issues
from humatheque.config import charger_yaml
from humatheque.regles.modeles import Regles
from humatheque.regles.moteur import choisir, conventions

Suite = Literal["creer", "convertir", "arreter"]


@lru_cache
def _cfg() -> dict[str, Any]:
    return charger_yaml("decision")


# Identifiants des règles avant le 06/11 (hérités de Grist) → nom en clair, pour
# relire les dossiers enregistrés ; les nouvelles décisions portent déjà le nom.
ANCIENS_NOMS = {
    "S01": "Notice déjà dans Alma", "S14": "Candidat Alma à vérifier", "P01": "Provenance à confirmer",
    "S10": "Résultat Sudoc à vérifier", "S02": "Thèse hors EPHE, originale imprimée au Sudoc",
    "S03": "Thèse hors EPHE, notice électronique au Sudoc",
    "S04": "Thèse hors EPHE, reproduction imprimée déjà au Sudoc",
    "S05": "Thèse hors EPHE, reproduction microfiche au Sudoc", "S06": "Thèse EPHE, notice électronique au Sudoc",
    "S07": "Thèse EPHE, reproduction au Sudoc", "S08": "Thèse EPHE absente du Sudoc",
    "S09": "Thèse hors EPHE absente du Sudoc", "S12": "Mémoire absent du Sudoc",
    "S13": "Mémoire, notice électronique au Sudoc",
}


class Decision(BaseModel):
    faits: dict[str, Any] = Field(default_factory=dict)
    situation: str | None = Field(None, description="nom de la règle de decisions.yaml retenue (« sinon » à défaut)")
    ligne: dict[str, Any] | None = Field(None, description="règle retenue, pour affichage")
    action: str | None = None
    suite: Suite
    profil_cible: str | None = None
    routage: str
    motif: str

    @field_validator("situation", mode="before")
    @classmethod
    def _ancien_nom(cls, v: Any) -> Any:
        return ANCIENS_NOMS.get(v, v) if isinstance(v, str) else v

    def texte_faits(self) -> str:
        return ", ".join(f"{k} = {v}" for k, v in self.faits.items())


class RoutageService(BaseModel):
    service: str
    statut: str
    routage: str | None = Field(None, description="issue ; None : jugé par les états (alma, sudoc)")
    bloquant: bool = False
    exception: bool = False
    alertes: list[str] = Field(default_factory=list)
    motif: str = ""


def faits_du_dossier(type_document: str, provenance: str | None, etat_alma: str | None,
                     etat_sudoc: str | None) -> dict[str, Any]:
    """Faits de décision (§7) ; correspondances dans config/regles.yaml (`faits`)."""
    corr = conventions()["faits"]
    return {"type": corr["type"].get(type_document, type_document),
            "provenance": corr["provenance"].get(provenance or "", provenance),
            "alma": etat_alma, "sudoc": etat_sudoc}


def decider(regles: Regles, faits: dict[str, Any]) -> Decision:
    c = choisir(regles, faits)
    nom = c.regle.get("nom")
    if nom == "sinon":
        motif = f"{c.motif} ({', '.join(f'{k} = {v}' for k, v in faits.items())})"
    else:
        motif = nom + (f" : {c.regle['motif']}" if c.regle.get("motif") and c.regle["motif"] != nom else "")
    return Decision(faits=faits, situation=nom, ligne=c.regle, action=c.action, suite=c.suite,
                    profil_cible=c.profil if c.suite in ("creer", "convertir") else None,
                    routage=c.issue, motif=motif)


def routage_service(service: str, statut: str) -> RoutageService:
    """Issue d'une réponse de service (`config/services.yaml`, clé du service : extraction,
    alma, sudoc, idref_personne, idref_organisations, dewey)."""
    cfg = charger_yaml("services").get(service) or {}
    alertes = list(filter(None, [(cfg.get("alertes") or {}).get(statut)]))
    statuts = cfg.get("statuts")
    if statuts is None:
        return RoutageService(service=service, statut=statut, alertes=alertes,
                              motif="jugé par les états du §6 et decisions.yaml")
    issue = statuts.get(statut)
    if issue is None:
        return RoutageService(service=service, statut=statut, routage=issues.INCIDENT, bloquant=True, alertes=alertes,
                              motif=f"{_cfg()['statut_inconnu']['motif']} : {service} / {statut}")
    a_reprendre = issue == issues.A_REPRENDRE
    bloquant = issue == issues.INCIDENT or (a_reprendre and not cfg.get("non_bloquant"))
    return RoutageService(service=service, statut=statut, routage=issue, bloquant=bloquant,
                          exception=a_reprendre, alertes=alertes, motif=f"{service} : {statut}")


def routage_final(routages: Iterable[str | None]) -> str:
    """Issue du dossier : la plus sévère de ses étapes."""
    return issues.la_plus_severe(routages)
