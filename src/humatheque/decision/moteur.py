"""Moteur de décision (SPEC §7).

- `decider` : clé `(etat_alma, etat_sudoc, provenance)` → première ligne de
  `Situations` qui correspond (`*` joker, alternatives « a | b », alias
  d'`etat_sudoc`) → suite du traitement (créer, convertir, arrêter) ;
- `routage_service` : `(service, statut)` → routage par `Statuts_services`
  (pour alma-checker et sudoc-checker, seule la ligne `error` est lue) ;
- `routage_final` : le plus sévère des routages des étapes.

Aucune situation ni aucun routage n'est écrit ici : tout vient de la spec et
de `config/decision.yaml`.
"""

from __future__ import annotations

from collections.abc import Iterable
from functools import lru_cache
from typing import Any, Literal

from pydantic import BaseModel, Field

from humatheque.config import charger_yaml
from humatheque.spec.modeles import Situation, Spec, decouper
from humatheque.spec.verificateur import JOKER, Vocabulaires

Suite = Literal["creer", "convertir", "arreter"]


@lru_cache
def _cfg() -> dict[str, Any]:
    return charger_yaml("decision")


class Cle(BaseModel):
    etat_alma: str
    etat_sudoc: str
    provenance: str

    def texte(self) -> str:
        return f"({self.etat_alma}, {self.etat_sudoc}, {self.provenance})"


class Decision(BaseModel):
    cle: Cle
    situation: str | None = Field(None, description="id de la ligne de Situations retenue")
    ligne: dict[str, str] | None = Field(None, description="ligne de Situations, pour affichage")
    action: str | None = None
    suite: Suite
    profil_cible: str | None = None
    routage: str
    motif: str


class RoutageService(BaseModel):
    service: str
    statut: str
    routage: str | None = Field(None, description="None : routage par les états (alma, sudoc)")
    bloquant: bool = False
    exception: bool = False
    alertes: list[str] = Field(default_factory=list)
    motif: str = ""


def _correspond(cellule: str, valeur: str, vocab: Vocabulaires, alias: dict[str, str] | None = None) -> bool:
    for v in decouper(cellule, vocab.sep_alternatives):
        v = (alias or {}).get(v, v)
        if v == JOKER or v == valeur:
            return True
    return False


def _suite(action: str) -> Suite | None:
    a = _cfg()["actions"]
    if action in a["creer"]:
        return "creer"
    if action in a["arreter"]:
        return "arreter"
    if action.startswith(a["prefixe_conversion"]):
        return "convertir"
    return None


def decider(spec: Spec, cle: Cle, vocab: Vocabulaires | None = None) -> Decision:
    vocab = vocab or Vocabulaires.depuis_config()
    cfg = _cfg()
    cle = cle.model_copy(update={"etat_sudoc": vocab.alias_sudoc.get(cle.etat_sudoc, cle.etat_sudoc)})
    s: Situation
    for s in spec.situations:
        if (_correspond(s.etat_alma, cle.etat_alma, vocab)
                and _correspond(s.etat_sudoc, cle.etat_sudoc, vocab, vocab.alias_sudoc)
                and _correspond(s.provenance, cle.provenance, vocab)):
            suite = _suite(s.action)
            ligne = s.model_dump()
            if suite is None:
                return Decision(cle=cle, situation=s.id, ligne=ligne, action=s.action, suite="arreter",
                                routage=cfg["non_prevue"]["routage"],
                                motif=f"action « {s.action} » inconnue ({s.id})")
            profils = decouper(s.profil_cible, vocab.sep_liste)
            profil = profils[0] if len(profils) == 1 else None
            if suite == "convertir":
                return Decision(cle=cle, situation=s.id, ligne=ligne, action=s.action, suite=suite,
                                profil_cible=profil, routage=cfg.get("routage_conversion") or s.routage,
                                motif=f"{s.id} : {s.action}")
            return Decision(cle=cle, situation=s.id, ligne=ligne, action=s.action, suite=suite,
                            profil_cible=profil if suite == "creer" else None,
                            routage=s.routage, motif=f"{s.id} : {s.action}")
    np = cfg["non_prevue"]
    return Decision(cle=cle, suite="arreter", routage=np["routage"], motif=f"{np['motif']} {cle.texte()}")


def routage_service(spec: Spec, service: str, statut: str, vocab: Vocabulaires | None = None) -> RoutageService:
    vocab = vocab or Vocabulaires.depuis_config()
    cfg = _cfg()
    alertes = list(filter(None, [(cfg.get("alertes_statut") or {}).get(service, {}).get(statut)]))
    lus = vocab.services_par_etats.get(service)
    if lus is not None and statut not in lus:
        return RoutageService(service=service, statut=statut, alertes=alertes,
                              motif="routage par les états et Situations")
    ligne = next((l for l in spec.statuts_services if l.service == service and l.statut == statut), None)
    if ligne is None:
        inc = cfg["statut_inconnu"]
        return RoutageService(service=service, statut=statut, routage=inc["routage"], bloquant=True,
                              alertes=alertes, motif=f"{inc['motif']} : {service} / {statut}")
    routage = ligne.routage
    non_bloquante = service in (cfg.get("decision_non_bloquante") or [])
    decision = routage == cfg["non_prevue"]["routage"]
    ordre = cfg["ordre_routages"]
    plus_severe = routage in ordre and ordre.index(routage) < ordre.index(cfg["non_prevue"]["routage"])
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
