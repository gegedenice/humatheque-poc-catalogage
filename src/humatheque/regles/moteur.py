"""Moteur des règles (SPEC §7, §8), d'après `reference/moteur-regles/moteur.py`.

- `evaluer_decisions` / `choisir` : faits → première règle de `decisions.yaml`
  qui correspond aux faits (`quand`), sinon `sinon` ;
- la génération de la notice est dans `regles/notice.py` (zones structurées).

Rien de métier ici : zones, codes, valeurs fixes et décisions viennent de
`regles/` ; les conventions de lecture de `config/regles.yaml`.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from pydantic import BaseModel, Field

from humatheque.config import charger_yaml
from humatheque.regles.modeles import RegleDecision, Regles


@lru_cache
def conventions() -> dict[str, Any]:
    return charger_yaml("regles")


# ------------------------------------------------------------------ décision
class Evaluation(BaseModel):
    nom: str
    si: str = Field(description="condition en clair")
    vraie: bool | None = Field(None, description="None : non évaluée (règle précédente retenue) ou erreur")
    erreur: str | None = None


class Traduction(BaseModel):
    """Chaîne code d'API → valeur du fait (banc d'essai, dossier)."""

    fait: str
    code: Any = None
    valeur: Any = None
    connue: bool = Field(True, description="false : code ni listé ni rattrapé (code_inconnu)")


class Choix(BaseModel):
    """Règle retenue pour des faits, et évaluation de chaque condition (banc d'essai)."""

    faits: dict[str, Any]
    traductions: list[Traduction] = Field(default_factory=list)
    regle: dict[str, Any] = Field(description="règle retenue (ou `sinon`, nom « sinon »)")
    action: str
    suite: str
    issue: str = Field(description="terminé ou à reprendre")
    profil: str | None = None
    motif: str
    evaluations: list[Evaluation] = Field(default_factory=list)


def _action(alors: str) -> dict[str, str]:
    from humatheque import issues

    return conventions()["actions"].get(alors) or {"suite": "arreter", "issue": issues.A_REPRENDRE}


def traduire(regles: Regles, codes: dict[str, Any]) -> list[Traduction]:
    """Codes d'API (ou libellés) → valeurs des faits du dictionnaire (`decisions.yaml`)."""
    sortie = []
    for nom, code in codes.items():
        f = regles.faits.get(nom)
        if f is None:
            sortie.append(Traduction(fait=nom, code=code, valeur=code, connue=False))
            continue
        valeur = f.traduire(code)
        connue = valeur is not None and (code in (None, "") or str(code) in f.valeurs
                                         or any(str(code) in c for c in f.valeurs.values()))
        sortie.append(Traduction(fait=nom, code=code, valeur=valeur, connue=connue))
    return sortie


def choisir(regles: Regles, faits: dict[str, Any], *, tout_evaluer: bool = False,
            codes: dict[str, Any] | None = None) -> Choix:
    """Première règle vraie ; `tout_evaluer` évalue aussi les suivantes (banc d'essai).
    `codes` : codes d'API bruts, traduits en faits par le dictionnaire (prioritaires)."""
    traductions = traduire(regles, codes) if codes else []
    faits = {**faits, **{t.fait: t.valeur for t in traductions}}
    retenue: RegleDecision | None = None
    evaluations = []
    for r in regles.decisions:
        if retenue is not None and not tout_evaluer:
            evaluations.append(Evaluation(nom=r.nom, si=r.texte_condition))
            continue
        vraie = r.correspond(faits)
        evaluations.append(Evaluation(nom=r.nom, si=r.texte_condition, vraie=vraie))
        if vraie and retenue is None:
            retenue = r
    if retenue is None:
        donnees = {"nom": "sinon", **regles.sinon}
    else:
        donnees = retenue.model_dump(exclude_none=True, exclude_defaults=True, by_alias=True)
        donnees["nom"] = retenue.nom
    alors = str(donnees.get("alors", "vérification"))
    a = _action(alors)
    motif = str(donnees.get("motif") or donnees["nom"])
    return Choix(faits=faits, traductions=traductions, regle=donnees, action=alors, suite=a["suite"], issue=a["issue"],
                 profil=donnees.get("profil"), motif=motif, evaluations=evaluations)
