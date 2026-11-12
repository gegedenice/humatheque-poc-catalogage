"""Vérificateur des règles (`humatheque check-spec`, SPEC §8.4), d'après
`reference/moteur-regles/moteur.py`. Signale sans rien corriger.

`verifier_textes` vérifie aussi des brouillons (interface d'administration) :
un YAML illisible donne des constats d'erreur au lieu d'une exception.
"""

from __future__ import annotations

import itertools
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel

from humatheque.regles.chargement import ReglesInvalides, charger_regles
from humatheque.regles.modeles import Regles
from humatheque.regles.modeles import GROUPES_ETAPES, Noeud, ZoneRegle
from humatheque.regles.moteur import conventions, evaluer, valeurs_possibles
from humatheque.regles.notice import RENDUS

Gravite = Literal["erreur", "avertissement", "info"]


class Constat(BaseModel):
    gravite: Gravite
    code: str
    table: str = ""     # fichier (pipeline.yaml, notice/…)
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


def _zone(regles: Regles, z: ZoneRegle, fichier: str, attributs: dict[str, set[str]], signaler) -> set[str]:
    """Contrôles d'une zone ; renvoie les variables utilisées (zone active)."""
    lieu = f"zone {z.id}"
    utilisees: set[str] = set()
    if not (len(z.zone) == 3 and z.zone.isdigit()):
        signaler("erreur", "zone_illisible", fichier, lieu, "l'étiquette doit compter 3 chiffres")
    if z.indicateurs is not None and len(z.indicateurs) != 2:
        signaler("erreur", "indicateurs_invalides", fichier, lieu, "les indicateurs comptent 2 caractères")
    autorites = [sz.autorite for sz in z.sous_zones if sz.autorite]
    if len(set(autorites)) > 1:
        signaler("erreur", "repetition_multiple", fichier, lieu,
                 f"une zone ne se répète que sur une autorité ({', '.join(sorted(set(autorites)))})")
    for sz in z.sous_zones:
        ou = f"{lieu} ${sz.code}"
        genres = [g for g, v in (("constante", sz.constante is not None), ("variable", sz.variable),
                                 ("composition", sz.parties), ("autorite", sz.autorite)) if v]
        if len(genres) > 1:
            signaler("erreur", "valeur_ambigue", fichier, ou, f"une seule sorte de valeur ({', '.join(genres)})")
        if not genres and z.actif:
            signaler("erreur", "valeur_absente", fichier, ou, "sous-zone sans valeur dans une zone active")
        if len(sz.code) != 1:
            signaler("erreur", "code_invalide", fichier, ou, "le code de sous-zone est un caractère")
        if sz.si_absente == "repli" and sz.repli is None:
            signaler("erreur", "repli_absent", fichier, ou, "« valeur de repli » choisie sans valeur")
        if sz.rendu and sz.rendu not in RENDUS:
            signaler("erreur", "rendu_inconnu", fichier, ou, f"mode de rendu « {sz.rendu} » inconnu")
        noms = [(sz.variable, sz.attribut)] if sz.variable else []
        noms += [(p.variable, p.attribut) for p in sz.parties or [] if p.variable]
        noms += [(sz.autorite, None)] if sz.autorite else []
        for n, a in noms:
            v = regles.variables.get(n)
            if v is None:
                signaler("erreur", "variable_inconnue", fichier, ou, f"variable inconnue « {n} »")
                continue
            if z.actif:
                utilisees.add(n)
            if a and a not in attributs.get(v.type, {"forme"}):
                signaler("erreur", "attribut_impossible", fichier, ou, f"attribut .{a} impossible pour {n} ({v.type})")
            if n == sz.autorite and v.type not in attributs:
                signaler("erreur", "autorite_invalide", fichier, ou, f"{n} n'est ni une personne ni un organisme")
    return utilisees


def verifier(regles: Regles) -> Rapport:
    conv = conventions()
    attributs = {t: set(a) for t, a in conv["attributs"].items()}
    constats: list[Constat] = []

    def signaler(gravite: Gravite, code: str, fichier: str, lieu: str, message: str) -> None:
        constats.append(Constat(gravite=gravite, code=code, table=fichier, ligne=lieu, message=message))

    # --- notice : socle et variantes
    for t, mode in regles.socle.rendus.items():
        if mode not in RENDUS:
            signaler("erreur", "rendu_inconnu", regles.socle.fichier, "rendus", f"{t} : mode « {mode} » inconnu")
    ids = [z.id for z in regles.socle.zones]
    for i in {i for i in ids if ids.count(i) > 1}:
        signaler("erreur", "zone_doublon", regles.socle.fichier, f"zone {i}", f"identifiant « {i} » en double")
    utilisees = set()
    for z in regles.socle.zones:
        utilisees |= _zone(regles, z, regles.socle.fichier, attributs, signaler)
    for nom, v in regles.variantes.items():
        for z in v.ajouter + v.remplacer:
            utilisees |= _zone(regles, z, v.fichier, attributs, signaler)
        for z in v.remplacer:
            if z.id not in ids:
                signaler("erreur", "zone_inconnue", v.fichier, f"zone {z.id}", "remplace une zone absente du socle")
        for i in v.retirer:
            if i not in ids:
                signaler("erreur", "zone_inconnue", v.fichier, f"zone {i}", "retire une zone absente du socle")
        for z in v.ajouter:
            if z.id in ids:
                signaler("erreur", "zone_doublon", v.fichier, f"zone {z.id}", "ajoute une zone déjà dans le socle (remplacer ?)")
    for n in sorted(set(regles.variables) - utilisees):
        signaler("info", "variable_inutilisee", "variables.yaml", n, f"{n} utilisée par aucune zone active")

    # --- pipeline
    notices_atteintes: set[str] = set()

    def noeud(etape: str, n: Noeud, ici: str, possibles: dict[str, list[str]]) -> None:
        for cle, v in [*n.si.items(), *n.sauf.items()]:
            if cle not in possibles:
                signaler("erreur", "variable_inconnue", "pipeline.yaml", ici,
                         f"« {cle} » n'est pas lue à l'étape {etape} ({', '.join(possibles)})")
                continue
            for x in Noeud.liste(v):
                if possibles[cle] and x not in possibles[cle]:
                    signaler("erreur", "valeur_inconnue", "pipeline.yaml", ici,
                             f"{cle} = « {x} » : valeur inconnue ({', '.join(possibles[cle])})")
        if n.branches and n.resultat:
            signaler("erreur", "noeud_ambigu", "pipeline.yaml", ici, "une branche a des sous-branches OU un résultat")
        if not n.branches and not n.resultat:
            signaler("erreur", "noeud_vide", "pipeline.yaml", ici, "branche sans résultat ni sous-branche")
        if n.resultat in ("conversion", "création"):
            if n.notice not in regles.variantes:
                signaler("erreur", "notice_inconnue", "pipeline.yaml", ici, f"notice cible inconnue « {n.notice} »")
            notices_atteintes.add(n.notice or "")
        for i, f in enumerate(n.branches, start=1):
            noeud(etape, f, f"{ici} › {f.condition}", possibles)
        if n.sinon is not None:
            noeud(etape, n.sinon, f"{ici} › sinon", possibles)

    for nom, e in regles.etapes.items():
        possibles = valeurs_possibles(regles, nom)
        for f in e.branches:
            noeud(nom, f, f"{e.libelle} › {f.condition}", possibles)
        noeud(nom, e.sinon, f"{e.libelle} › sinon", possibles)
    # --- ordre des étapes (déplaçables dans le studio, à l'intérieur de leur groupe)
    rang = {e: i for i, g in enumerate(GROUPES_ETAPES) for e in g}
    noms = list(regles.etapes)
    for a, b in zip(noms, noms[1:]):
        if rang.get(a, 0) > rang.get(b, 0):
            signaler("erreur", "ordre_etapes", "pipeline.yaml", regles.etapes[b].libelle,
                     f"{regles.etapes[b].libelle} ne peut pas précéder {regles.etapes[a].libelle} "
                     "(vérifications, puis IdRef et Dewey, puis création)")
    if "provenance" in noms:
        for nom in noms[:noms.index("provenance")]:
            if "ephe" in _variables_lues(regles.etapes[nom]):
                signaler("erreur", "ephe_pas_encore_connue", "pipeline.yaml", regles.etapes[nom].libelle,
                         f"{regles.etapes[nom].libelle} teste « ephe », calculée plus tard à l'étape "
                         f"{regles.etapes['provenance'].libelle} : placer la provenance avant")

    for p in sorted(set(regles.variantes) - notices_atteintes):
        signaler("avertissement", "notice_non_atteinte", regles.variantes[p].fichier, "",
                 f"notice {p} atteinte par aucune branche du pipeline")

    # --- cas qui tombent dans le « sinon » d'une étape (combinaisons des valeurs possibles)
    for nom, e in regles.etapes.items():
        if not e.branches or e.sinon.resultat == "poursuite":
            continue
        for texte in cas_sinon(regles, nom):
            signaler("info", "cas_sinon", "pipeline.yaml", e.libelle, f"tombe dans « sinon » : {texte}")

    for a in regles.avertissements:
        signaler("avertissement", "chargement", "", "", a)

    return Rapport(emplacement=regles.emplacement, version=regles.version, constats=constats)


def _variables_lues(e) -> set[str]:
    """Variables testées par l'arbre d'une étape (si / sauf, à toutes les profondeurs)."""
    lues: set[str] = set()

    def parcourir(n: Noeud) -> None:
        lues.update(n.si, n.sauf)
        for f in n.branches:
            parcourir(f)
        if n.sinon is not None:
            parcourir(n.sinon)

    for f in e.branches:
        parcourir(f)
    parcourir(e.sinon)
    return lues


def cas_sinon(regles: Regles, etape: str) -> list[str]:
    """Combinaisons qui tombent dans le « sinon » de l'étape, sans les variables sans effet."""
    possibles = {k: v for k, v in valeurs_possibles(regles, etape).items() if v}
    if etape != "provenance" and "ephe" in possibles:  # après la provenance, ephe vaut oui ou non
        possibles["ephe"] = [x for x in possibles["ephe"] if x in ("oui", "non")]
    cles = list(possibles)
    sortie: set[str] = set()
    for combo in itertools.product(*(possibles[k] for k in cles)):
        valeurs = dict(zip(cles, combo))
        if not evaluer(regles, etape, valeurs).sinon:
            continue
        utiles = {k: v for k, v in valeurs.items()
                  if any(not evaluer(regles, etape, {**valeurs, k: autre}).sinon for autre in possibles[k] if autre != v)}
        sortie.add(", ".join(f"{k} = {v}" for k, v in utiles.items()) or "toutes valeurs")
    return sorted(sortie)


def rapport_invalide(e: ReglesInvalides, emplacement: str) -> Rapport:
    return Rapport(emplacement=emplacement, version="-", constats=[
        Constat(gravite="erreur", code="yaml_invalide", table=f, ligne=l, message=m) for f, l, m in e.problemes])


def verifier_textes(brouillons: dict[str, str], reglages: Any = None, dossier: Path | None = None) -> Rapport:
    """Vérifie les règles du dossier avec certains fichiers remplacés par des brouillons."""
    try:
        regles = charger_regles(reglages, dossier=dossier, brouillons=brouillons)
    except ReglesInvalides as e:
        return rapport_invalide(e, str(dossier or ""))
    return verifier(regles)
