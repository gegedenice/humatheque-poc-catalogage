"""Vérificateur des règles (`humatheque check-spec`, SPEC §8.4), d'après
`reference/moteur-regles/moteur.py`. Signale sans rien corriger.

`verifier_textes` vérifie aussi des brouillons (interface d'administration) :
un YAML illisible donne des constats d'erreur au lieu d'une exception.
"""

from __future__ import annotations

import itertools
from pathlib import Path
from typing import Any, Literal

import rule_engine
from pydantic import BaseModel

from humatheque.regles.chargement import ReglesInvalides, charger_regles
from humatheque.regles.modeles import Regles
from humatheque.regles.moteur import LIGNE, VAR, choisir, conventions, regle

Gravite = Literal["erreur", "avertissement", "info"]


class Constat(BaseModel):
    gravite: Gravite
    code: str
    table: str = ""     # fichier (profils/…, decisions.yaml…)
    ligne: str = ""     # lieu dans le fichier
    message: str


class Rapport(BaseModel):
    emplacement: str
    version: str
    constats: list[Constat]

    @property
    def erreurs(self) -> list[Constat]:
        return [c for c in self.constats if c.gravite == "erreur"]

    @property
    def avertissements(self) -> list[Constat]:
        return [c for c in self.constats if c.gravite == "avertissement"]

    @property
    def bloquant(self) -> bool:
        return bool(self.erreurs)


def _condition_invalide(condition: str) -> str | None:
    try:
        regle(condition)
    except rule_engine.errors.EngineError as e:
        return getattr(e, "message", str(e))
    return None


def verifier(regles: Regles) -> Rapport:
    conv = conventions()
    attributs = {t: set(a) for t, a in conv["attributs"].items()}
    constats: list[Constat] = []

    def signaler(gravite: Gravite, code: str, fichier: str, lieu: str, message: str) -> None:
        constats.append(Constat(gravite=gravite, code=code, table=fichier, ligne=lieu, message=message))

    # --- profils
    for nom, p in regles.profils.items():
        declarees, utilisees = set(p.variables), set()
        for l in p.lignes:
            lieu = f"ligne {l.numero} ({l.etiquette})"
            for g in (s.strip() for s in l.ligne.split("||")):
                if not LIGNE.match(g):
                    signaler("erreur", "zone_illisible", p.fichier, lieu,
                             "la zone doit commencer par une étiquette sur 3 chiffres")
            iterees, listes_ligne = set(), set()
            for n, a in VAR.findall(l.ligne):
                v = regles.variables.get(n)
                if v is None:
                    signaler("erreur", "variable_inconnue", p.fichier, lieu, f"variable inconnue {{{n}}}")
                    continue
                if l.actif:
                    utilisees.add(n)
                if a and a not in attributs.get(v.type, set()):
                    signaler("erreur", "attribut_impossible", p.fichier, lieu, f"attribut .{a} impossible pour {n}")
                if a and v.type in attributs:
                    iterees.add(n)
                if v.liste and not a:
                    listes_ligne.add(n)
            if len(iterees) > 1:
                signaler("erreur", "repetition_multiple", p.fichier, lieu,
                         f"une ligne ne se répète que sur une variable ({', '.join(sorted(iterees))})")
            for n in l.joindre:
                if n not in listes_ligne:
                    signaler("erreur", "joindre_invalide", p.fichier, lieu,
                             f"joindre : {n} n'est pas une variable liste utilisée seule dans la ligne")
            if l.si and (err := _condition_invalide(l.si)):
                signaler("erreur", "condition_invalide", p.fichier, lieu, f"condition invalide ({err})")
        for n in sorted(utilisees - declarees):
            signaler("avertissement", "variable_non_declaree", p.fichier, "",
                     f"{n} utilisée mais absente de la liste « variables »")
        for n in sorted(declarees - utilisees):
            signaler("info", "variable_inutilisee", p.fichier, "", f"{n} déclarée mais utilisée par aucune ligne active")

    # --- décisions
    vus: set[str] = set()
    for r in regles.decisions:
        lieu = f"« {r.nom} »"
        if r.nom in vus:
            signaler("erreur", "decision_doublon", "decisions.yaml", lieu, f"nom « {r.nom} » en double")
        vus.add(r.nom)
        if err := _condition_invalide(r.si):
            signaler("erreur", "condition_invalide", "decisions.yaml", lieu, f"condition invalide ({err})")
        if r.alors not in conv["actions"]:
            signaler("erreur", "action_inconnue", "decisions.yaml", lieu, f"action « {r.alors} » inconnue")
        elif conv["actions"][r.alors]["suite"] in ("creer", "convertir") and r.profil not in regles.profils:
            signaler("erreur", "profil_inconnu", "decisions.yaml", lieu, f"profil inconnu {r.profil}")
    if regles.sinon.get("alors") and regles.sinon["alors"] not in conv["actions"]:
        signaler("erreur", "action_inconnue", "decisions.yaml", "sinon", f"action « {regles.sinon['alors']} » inconnue")
    atteints = {r.profil for r in regles.decisions}
    for p in sorted(set(regles.profils) - atteints):
        signaler("avertissement", "profil_non_atteint", regles.profils[p].fichier, "", f"profil {p} atteint par aucune décision")

    # --- complétude
    comp = conv["completude"]
    noms = [n for n in comp["faits"] if n in regles.faits]
    for combo in itertools.product(*(regles.faits[n] for n in noms)):
        faits = {**dict(zip(noms, combo)), **comp.get("fixes", {})}
        if comp.get("incompatibles") and regle(comp["incompatibles"]).matches(faits):
            continue
        if choisir(regles, faits).regle["nom"] == "sinon":
            signaler("avertissement", "cas_non_couvert", "decisions.yaml", "",
                     "cas non couvert : " + ", ".join(f"{k} = {v}" for k, v in faits.items()))

    for a in regles.avertissements:
        signaler("avertissement", "chargement", "", "", a)

    return Rapport(emplacement=regles.emplacement, version=regles.version, constats=constats)


def rapport_invalide(e: ReglesInvalides, emplacement: str) -> Rapport:
    return Rapport(emplacement=emplacement, version="—", constats=[
        Constat(gravite="erreur", code="yaml_invalide", table=f, ligne=l, message=m) for f, l, m in e.problemes])


def verifier_textes(brouillons: dict[str, str], reglages: Any = None, dossier: Path | None = None) -> Rapport:
    """Vérifie les règles du dossier avec certains fichiers remplacés par des brouillons."""
    try:
        regles = charger_regles(reglages, dossier=dossier, brouillons=brouillons)
    except ReglesInvalides as e:
        return rapport_invalide(e, str(dossier or ""))
    return verifier(regles)
