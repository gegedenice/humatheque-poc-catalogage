"""Moteur des règles (SPEC §7, §8), d'après `reference/moteur-regles/moteur.py`.

- `evaluer_decisions` / `choisir` : faits → première règle de `decisions.yaml`
  dont la condition rule-engine est vraie, sinon `sinon` ;
- `generer` : profil + variables du document → notice, chaque zone annotée par
  sa ligne de profil (`ref`, `statut`).

Rien de métier ici : zones, codes, valeurs fixes et décisions viennent de
`regles/` ; les conventions de lecture de `config/regles.yaml`.
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Any

import rule_engine
from pydantic import BaseModel, Field

from humatheque.config import charger_yaml
from humatheque.generateur.modeles import Notice, RegleNonCouverte, SousZone, Zone
from humatheque.regles.modeles import Ligne, Profil, RegleDecision, Regles

VAR = re.compile(r"\{(\w+)(?:\.(\w+))?\}")
LIGNE = re.compile(r"^(\d{3})(?: ([0-9#]{2})?)?(.*)$")


class GenerationEchec(Exception):
    """Échec du générateur (statut d'étape `generation_echec`)."""


@lru_cache
def conventions() -> dict[str, Any]:
    return charger_yaml("regles")


_CONTEXTE = rule_engine.Context(default_value=None)


@lru_cache(maxsize=1024)
def regle(condition: str) -> rule_engine.Rule:
    """Condition rule-engine compilée (symbole inconnu → null)."""
    return rule_engine.Rule(condition, context=_CONTEXTE)


# ------------------------------------------------------------------ décision
class Evaluation(BaseModel):
    id: str
    si: str
    vraie: bool | None = Field(None, description="None : non évaluée (règle précédente retenue) ou erreur")
    erreur: str | None = None


class Choix(BaseModel):
    """Règle retenue pour des faits, et évaluation de chaque condition (banc d'essai)."""

    faits: dict[str, Any]
    regle: dict[str, Any] = Field(description="règle retenue (ou `sinon`, id « sinon »)")
    action: str
    suite: str
    routage: str
    profil: str | None = None
    motif: str
    evaluations: list[Evaluation] = Field(default_factory=list)


def _action(alors: str) -> dict[str, str]:
    return conventions()["actions"].get(alors) or {"suite": "arreter", "routage": "décision"}


def choisir(regles: Regles, faits: dict[str, Any], *, tout_evaluer: bool = False) -> Choix:
    """Première règle vraie ; `tout_evaluer` évalue aussi les suivantes (banc d'essai)."""
    retenue: RegleDecision | None = None
    evaluations = []
    for r in regles.decisions:
        if retenue is not None and not tout_evaluer:
            evaluations.append(Evaluation(id=r.id, si=r.si))
            continue
        try:
            vraie = regle(r.si).matches(faits)
            evaluations.append(Evaluation(id=r.id, si=r.si, vraie=vraie))
        except rule_engine.errors.EngineError as e:
            evaluations.append(Evaluation(id=r.id, si=r.si, erreur=getattr(e, "message", str(e))))
            continue
        if vraie and retenue is None:
            retenue = r
    if retenue is None:
        s = regles.sinon
        donnees = {"id": "sinon", **s}
    else:
        donnees = retenue.model_dump(exclude_none=True)
    alors = str(donnees.get("alors", "décision"))
    a = _action(alors)
    routage = str(donnees.get("routage") or a["routage"])
    motif = str(donnees.get("motif") or f"{donnees['id']} : {alors}" + (f" ({donnees['profil']})" if donnees.get("profil") else ""))
    return Choix(faits=faits, regle=donnees, action=alors, suite=a["suite"], routage=routage,
                 profil=donnees.get("profil"), motif=motif, evaluations=evaluations)


# ------------------------------------------------------------------ génération
def _manque(x: Any) -> bool:
    return x is None or x == "" or x == [] or x == {}


def _valeur(ctx: dict[str, Any], nom: str, attr: str | None, element: tuple[str, Any] | None,
            joindre: dict[str, list[str]]) -> Any:
    obj = element[1] if element and element[0] == nom else ctx.get(nom)
    if isinstance(obj, dict):
        return obj.get(attr or "forme")
    if isinstance(obj, list) and attr is None:
        vals = [o.get("forme") if isinstance(o, dict) else o for o in obj]
        vals = [str(v) for v in vals if not _manque(v)]
        if not vals:
            return None
        if nom in joindre:
            seps = joindre[nom]
            sep, dernier = seps[0], (seps[1] if len(seps) > 1 else seps[0])
            return vals[0] if len(vals) == 1 else sep.join(vals[:-1]) + dernier + vals[-1]
        return vals
    return obj if attr is None else None


def _rendre(gabarit: str, ctx: dict[str, Any], element: tuple[str, Any] | None,
            joindre: dict[str, list[str]]) -> tuple[Zone | None, bool]:
    """(zone ou None, complet). Une sous-zone dont une variable manque est omise ;
    la zone est omise si toutes ses sous-zones à variable le sont."""
    m = LIGNE.match(gabarit.strip())
    if not m:
        raise GenerationEchec(f"ligne illisible : {gabarit!r}")
    etiquette, ind, reste = m.groups()
    sous_zones: list[tuple[str, str]] = []
    complet, avec_var, rendue = True, False, False
    for sz in [s for s in reste.split("$") if s]:
        code, texte = sz[0], sz[1:]
        vars_ = VAR.findall(sz)
        if not vars_:
            sous_zones.append((code, texte))
            continue
        avec_var = True
        vals = {v: _valeur(ctx, v[0], v[1] or None, element, joindre) for v in vars_}
        if any(_manque(x) for x in vals.values()):
            complet = False
            continue
        n = max((len(x) for x in vals.values() if isinstance(x, list)), default=1)
        for i in range(n):
            def remplacer(mm: re.Match) -> str:
                x = vals[(mm[1], mm[2] or "")]
                return str(x[i] if isinstance(x, list) else x)
            sous_zones.append((code, VAR.sub(remplacer, texte)))
        rendue = True
    if avec_var and not rendue:
        return None, False
    controle = ind is None
    return Zone(etiquette=etiquette, indicateurs=None if controle else ind,
                sous_zones=[SousZone(code=c, valeur=v, regle="") for c, v in sous_zones]), complet


def productible(regles: Regles, l: Ligne) -> bool:
    """Une ligne active peut-elle produire une zone ? Non si chacune de ses formes
    (principale, repli) a toutes ses sous-zones à variable sur des variables
    indisponibles (`disponible: false`)."""
    if not l.actif:
        return False
    for gabarit in filter(None, (l.principal, l.repli)):
        m = LIGNE.match(gabarit)
        if not m:
            continue
        sous_zones = [sz for sz in m[3].split("$") if sz]
        avec_var = [sz for sz in sous_zones if VAR.search(sz)]
        if not avec_var or any(all(regles.variables.get(n) is None or regles.variables[n].disponible
                                    for n, _ in VAR.findall(sz)) for sz in avec_var):
            return True
    return False


def variable_iteree(regles: Regles, l: Ligne) -> str | None:
    """Variable personne / organisme dont un attribut est utilisé : une zone par élément."""
    types = set(conventions()["attributs"])
    return next((n for n, a in VAR.findall(l.ligne) if a and regles.variables.get(n)
                 and regles.variables[n].type in types), None)


def generer(regles: Regles, profil: str, ctx: dict[str, Any], statuts_exclus: list[str] | None = None) -> Notice:
    if profil not in regles.profils:
        raise GenerationEchec(f"profil inconnu : {profil}")
    p: Profil = regles.profils[profil]
    exclus = set(statuts_exclus or [])
    defaut = conventions().get("statut_par_defaut", "seed")
    notice = Notice(profil=profil, version_spec=regles.version)
    for l in p.lignes:
        statut = l.statut or defaut
        if not l.actif:
            notice.non_couvertes.append(RegleNonCouverte(regle=l.trace, zone=l.etiquette, statut=statut))
            continue
        if statut in exclus:
            continue
        if l.si:
            try:
                if not regle(l.si).matches(ctx):
                    continue
            except rule_engine.errors.EngineError as e:
                raise GenerationEchec(f"{profil}, {l.trace} : condition invalide ({e})") from e
        iteree = variable_iteree(regles, l)
        if iteree:
            elements = ctx.get(iteree)
            elements = elements if isinstance(elements, list) else ([elements] if not _manque(elements) else [])
        else:
            elements = [None]
        produit = False
        for el in elements:
            e = (iteree, el) if iteree else None
            zone, complet = _rendre(l.principal, ctx, e, l.joindre)
            if not complet and l.repli:
                zone, _ = _rendre(l.repli, ctx, e, l.joindre)
                if zone:
                    notice.replis.append(l.trace)
            if zone:
                for sz in zone.sous_zones:
                    sz.regle = l.trace
                zone.ajouter_regle(l.trace, statut)
                zone.libelle = iteree
                notice.zones.append(zone)
                produit = True
        if not produit and l.priorite == 1:
            notice.alertes.append(f"regle_prioritaire_omise:{l.trace}")
    notice.trace.append(f"profil {profil} : {len(notice.zones)} zone(s)")
    return notice
