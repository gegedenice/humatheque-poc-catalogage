"""Moteur de décision (SPEC §7).

- `faits_du_dossier` : type, provenance, alma, sudoc (valeurs de `config/regles.yaml`) ;
- `decider` : faits → première règle de `regles/decisions.yaml` dont la condition
  rule-engine est vraie (sinon `sinon`) → suite du traitement (créer, convertir,
  arrêter) et routage ;
- `routage_service` : `(service, statut)` → routage par `Statuts_services`
  (pour alma-checker et sudoc-checker, seule la ligne `error` est lue) ;
- `routage_final` : le plus sévère des routages des étapes.

Aucune décision ni aucun routage n'est écrit ici : tout vient de `regles/`,
`Statuts_services` et `config/decision.yaml`.
"""

from __future__ import annotations

from collections.abc import Iterable
from functools import lru_cache
from typing import Any, Literal

from pydantic import BaseModel, Field

from humatheque.config import charger_yaml
from humatheque.regles.modeles import Regles
from humatheque.regles.moteur import choisir, conventions

Suite = Literal["creer", "convertir", "arreter"]


@lru_cache
def _cfg() -> dict[str, Any]:
    return charger_yaml("decision")


class Decision(BaseModel):
    faits: dict[str, Any] = Field(default_factory=dict)
    situation: str | None = Field(None, description="id de la règle de decisions.yaml retenue (« sinon » à défaut)")
    ligne: dict[str, Any] | None = Field(None, description="règle retenue, pour affichage")
    action: str | None = None
    suite: Suite
    profil_cible: str | None = None
    routage: str
    motif: str

    def texte_faits(self) -> str:
        return ", ".join(f"{k} = {v}" for k, v in self.faits.items())


class RoutageService(BaseModel):
    service: str
    statut: str
    routage: str | None = Field(None, description="None : routage par les états (alma, sudoc)")
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
    situation = c.regle.get("id")
    motif = c.motif
    if situation == "sinon":
        motif = f"{c.motif} ({', '.join(f'{k} = {v}' for k, v in faits.items())})"
    elif c.suite in ("creer", "convertir") or not c.regle.get("motif"):
        motif = f"{situation} : {c.action}" + (f" ({c.profil})" if c.profil else "")
    else:
        motif = f"{situation} : {c.motif}"
    return Decision(faits=faits, situation=situation, ligne=c.regle, action=c.action, suite=c.suite,
                    profil_cible=c.profil if c.suite in ("creer", "convertir") else None,
                    routage=c.routage, motif=motif)


def routage_service(regles: Regles, service: str, statut: str) -> RoutageService:
    cfg = _cfg()
    alertes = list(filter(None, [(cfg.get("alertes_statut") or {}).get(service, {}).get(statut)]))
    lus = conventions()["services_routage_par_etats"].get(service)
    if lus is not None and statut not in lus:
        return RoutageService(service=service, statut=statut, alertes=alertes,
                              motif="routage par les états et decisions.yaml")
    ligne = next((l for l in regles.statuts_services if l.service == service and l.statut == statut), None)
    if ligne is None:
        inc = cfg["statut_inconnu"]
        return RoutageService(service=service, statut=statut, routage=inc["routage"], bloquant=True,
                              alertes=alertes, motif=f"{inc['motif']} : {service} / {statut}")
    routage = ligne.routage
    non_bloquante = service in (cfg.get("decision_non_bloquante") or [])
    decision = routage == cfg["routage_decision"]
    ordre = cfg["ordre_routages"]
    plus_severe = routage in ordre and ordre.index(routage) < ordre.index(cfg["routage_decision"])
    bloquant = plus_severe or (decision and not non_bloquante)
    return RoutageService(service=service, statut=statut, routage=routage, bloquant=bloquant,
                          exception=decision, alertes=alertes, motif=ligne.signification)


def routage_final(routages: Iterable[str | None]) -> str:
    ordre = _cfg()["ordre_routages"]
    presents = {r for r in routages if r}
    inconnus = presents - set(ordre)
    if inconnus:
        raise ValueError(f"routage(s) inconnu(s) : {', '.join(sorted(inconnus))}")
    return next((r for r in ordre if r in presents), ordre[-1])
