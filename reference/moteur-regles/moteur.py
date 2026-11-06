"""Moteur de règles minimal : décision (rule-engine) + notices-modèles (YAML).

Dépendances : pyyaml, rule-engine. Rien de métier n'est écrit ici : tout vient
de regles/variables.yaml, regles/profils/*.yaml et regles/decisions.yaml.

    python moteur.py verifier
    python moteur.py decider type=thèse provenance=EPHE alma=absent sudoc=rien
    python moteur.py generer these-originale-imprimee exemple_contexte.json
"""
from __future__ import annotations

import itertools
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import rule_engine
import yaml

VAR = re.compile(r"\{(\w+)(?:\.(\w+))?\}")
LIGNE = re.compile(r"^(\d{3})(?: ([0-9#]{2})?)?(.*)$")
ATTRIBUTS = {"personne": {"forme", "nom", "prenom", "ppn"}, "organisme": {"forme", "ppn"}}


@dataclass
class Regles:
    variables: dict
    profils: dict
    faits: dict
    decisions: list
    sinon: dict


def charger(dossier: Path) -> Regles:
    lire = lambda p: yaml.safe_load(p.read_text(encoding="utf-8"))
    variables = lire(dossier / "variables.yaml")
    profils = {}
    for f in sorted((dossier / "profils").glob("*.yaml")):
        p = lire(f)
        p["lignes"] = [l if isinstance(l, dict) else {"ligne": l} for l in p["lignes"]]
        profils[p["profil"]] = p
    d = lire(dossier / "decisions.yaml")
    return Regles(variables, profils, d["faits"], d["regles"], d["sinon"])


# ------------------------------------------------------------------ décision
def decider(regles: Regles, faits: dict) -> dict:
    for r in regles.decisions:
        if rule_engine.Rule(r["si"]).matches(faits):
            return r
    return {"id": "sinon", **regles.sinon}


# ------------------------------------------------------------------ génération
def _manque(x) -> bool:
    return x is None or x == "" or x == []


def _valeur(ctx, nom, attr, element):
    obj = element[1] if element and element[0] == nom else ctx.get(nom)
    if isinstance(obj, dict):
        return obj.get(attr or "forme")
    if isinstance(obj, list) and attr is None:
        vals = [o.get("forme") if isinstance(o, dict) else o for o in obj]
        return [v for v in vals if not _manque(v)] or None
    return obj if attr is None else None


def _rendre(gabarit: str, ctx, element):
    """Renvoie (zone ou None, complet). Une sous-zone dont une variable manque est omise ;
    la zone est omise si toutes ses sous-zones à variable le sont."""
    etiquette, ind, reste = LIGNE.match(gabarit.strip()).groups()
    parties, complet, avec_var, rendue = [], True, False, False
    for sz in [s for s in reste.split("$") if s]:
        vars_ = VAR.findall(sz)
        if not vars_:
            parties.append("$" + sz)
            continue
        avec_var = True
        vals = {v: _valeur(ctx, v[0], v[1] or None, element) for v in vars_}
        if any(_manque(x) for x in vals.values()):
            complet = False
            continue
        n = max((len(x) for x in vals.values() if isinstance(x, list)), default=1)
        for i in range(n):
            parties.append("$" + VAR.sub(
                lambda m: str(vals[(m[1], m[2] or "")][i] if isinstance(vals[(m[1], m[2] or "")], list)
                              else vals[(m[1], m[2] or "")]), sz))
        rendue = True
    if avec_var and not rendue:
        return None, False
    return f"{etiquette} {ind or ''}{''.join(parties)}", complet


def generer(regles: Regles, profil: str, ctx: dict):
    """Renvoie (zones, alertes, non_couvertes)."""
    zones, alertes, non_couvertes = [], [], []
    for l in regles.profils[profil]["lignes"]:
        principal, _, repli = (s.strip() for s in l["ligne"].partition("||"))
        trace = f"{principal[:3]} [{l.get('ref', '')}]"
        if l.get("actif") is False:
            non_couvertes.append(trace)
            continue
        if "si" in l and not rule_engine.Rule(l["si"]).matches(ctx):
            continue
        iteree = next((n for n, a in VAR.findall(l["ligne"])
                       if a and regles.variables.get(n, {}).get("type") in ATTRIBUTS), None)
        elements = ctx.get(iteree) if iteree else [None]
        elements = elements if isinstance(elements, list) else ([elements] if elements else [])
        produit = False
        for el in elements:
            e = (iteree, el) if iteree else None
            zone, complet = _rendre(principal, ctx, e)
            if not complet and repli:
                zone, _ = _rendre(repli, ctx, e)
            if zone:
                zones.append(zone)
                produit = True
        if not produit and l.get("priorite") == 1:
            alertes.append(f"{trace} : zone de priorité 1 non produite")
    zones.sort(key=lambda z: z[:3])
    return zones, alertes, non_couvertes


# ------------------------------------------------------------------ vérification
def verifier(regles: Regles) -> list[tuple[str, str]]:
    msgs = []
    for nom, p in regles.profils.items():
        declarees = set(p.get("variables", []))
        utilisees = set()
        for i, l in enumerate(p["lignes"], 1):
            lieu = f"{nom}, ligne {i} ({l['ligne'][:3]})"
            for g in (s.strip() for s in l["ligne"].split("||")):
                if not LIGNE.match(g):
                    msgs.append(("erreur", f"{lieu} : la zone doit commencer par une étiquette sur 3 chiffres"))
            listes = set()
            for n, a in VAR.findall(l["ligne"]):
                v = regles.variables.get(n)
                if v is None:
                    msgs.append(("erreur", f"{lieu} : variable inconnue {{{n}}}"))
                    continue
                if l.get("actif") is not False:
                    utilisees.add(n)
                if a and a not in ATTRIBUTS.get(v.get("type", "texte"), set()):
                    msgs.append(("erreur", f"{lieu} : attribut .{a} impossible pour {n}"))
                if a and v.get("type") in ATTRIBUTS:
                    listes.add(n)
            if len(listes) > 1:
                msgs.append(("erreur", f"{lieu} : une ligne ne se répète que sur une variable ({', '.join(sorted(listes))})"))
            if "si" in l:
                try:
                    rule_engine.Rule(l["si"])
                except rule_engine.errors.EngineError as e:
                    msgs.append(("erreur", f"{lieu} : condition invalide ({e.message})"))
        for n in sorted(utilisees - declarees):
            msgs.append(("avertissement", f"{nom} : {n} utilisée mais absente de la liste « variables »"))
        for n in sorted(declarees - utilisees):
            msgs.append(("info", f"{nom} : {n} déclarée mais utilisée par aucune ligne active"))

    for r in regles.decisions:
        try:
            rule_engine.Rule(r["si"])
        except rule_engine.errors.EngineError as e:
            msgs.append(("erreur", f"décision {r['id']} : condition invalide ({e.message})"))
        if r["alors"] in ("création", "conversion") and r.get("profil") not in regles.profils:
            msgs.append(("erreur", f"décision {r['id']} : profil inconnu {r.get('profil')}"))
    atteints = {r.get("profil") for r in regles.decisions}
    for p in sorted(set(regles.profils) - atteints):
        msgs.append(("avertissement", f"profil {p} atteint par aucune décision"))

    noms = [n for n in ("type", "provenance", "sudoc") if n in regles.faits]
    for combo in itertools.product(*(regles.faits[n] for n in noms)):
        faits = dict(zip(noms, combo), alma="absent")
        if (faits["type"] == "mémoire") != (faits["provenance"] == "sans objet"):
            continue
        if decider(regles, faits)["id"] == "sinon":
            msgs.append(("avertissement", "cas non couvert : " + ", ".join(f"{k} = {v}" for k, v in faits.items())))
    return msgs


if __name__ == "__main__":
    dossier = Path(os.environ.get("REGLES", Path(__file__).resolve().parents[2] / "regles"))
    regles = charger(dossier)
    cmd, *args = sys.argv[1:] or ["verifier"]
    if cmd == "verifier":
        msgs = verifier(regles)
        print(f"{len(regles.profils)} profils, {len(regles.decisions)} décisions")
        for g, m in msgs:
            print(f"  [{g}] {m}")
        sys.exit(1 if any(g == "erreur" for g, _ in msgs) else 0)
    if cmd == "decider":
        print(decider(regles, dict(a.split("=", 1) for a in args)))
    if cmd == "generer":
        zones, alertes, nc = generer(regles, args[0], json.loads(Path(args[1]).read_text(encoding="utf-8")))
        print("\n".join(zones), "\n\nAlertes :", alertes or "aucune", "\nNon couvertes :", ", ".join(nc))
