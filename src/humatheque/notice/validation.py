"""Validation d'une notice éditée (SPEC §9.2), affichée en marqueurs Monaco :

- syntaxe : étiquette sur 3 chiffres, indicateurs, sous-zones $x non vides ;
- règles du profil du dossier (`regles/profils/`) : zones de priorité 1
  absentes (erreur), zones ou sous-zones produites par aucune ligne du profil
  (avertissement), PPN mal formés ($3, erreur).

Les zones et sous-zones connues viennent des lignes du profil : aucune liste
UNIMARC dans le code.
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Literal

from pydantic import BaseModel

from humatheque.notice.analyse import analyser
from humatheque.regles.modeles import Regles
from humatheque.regles.moteur import LIGNE, productible

RE_PPN = re.compile(r"\d{8}[\dX]")
CODE_PPN = "3"  # sous-zone de lien vers une autorité (PPN) dans les lignes des profils


class Marqueur(BaseModel):
    ligne: int
    colonne_debut: int = 1
    colonne_fin: int = 1
    gravite: Literal["erreur", "avertissement"]
    message: str


class ProfilNotice(BaseModel):
    """Ce que les règles d'un profil produisent."""

    prioritaires: dict[str, list[str]]  # étiquette → règles de priorité 1
    sous_zones: dict[str, set[str]]  # étiquette → codes connus
    etiquettes: set[str]


def profil_notice(regles: Regles, profil: str) -> ProfilNotice | None:
    """Zones et sous-zones documentées par les lignes du profil, y compris inactives ;
    zones de priorité 1 des lignes qui peuvent produire une zone (None : profil inconnu)."""
    p = regles.profils.get(profil)
    if p is None:
        return None
    prioritaires: dict[str, list[str]] = defaultdict(list)
    sous_zones: dict[str, set[str]] = defaultdict(set)
    etiquettes: set[str] = set()
    for l in p.lignes:
        etiquettes.add(l.etiquette)
        for gabarit in filter(None, (l.principal, l.repli)):
            m = LIGNE.match(gabarit)
            if m:
                sous_zones[m[1]].update(sz[0] for sz in m[3].split("$") if sz)
        if l.priorite == 1 and productible(regles, l):
            prioritaires[l.etiquette].append(l.trace)
    return ProfilNotice(prioritaires=dict(prioritaires), sous_zones=dict(sous_zones), etiquettes=etiquettes)


def valider(texte: str, regles: Regles | None = None, profil: str | None = None) -> list[Marqueur]:
    marqueurs: list[Marqueur] = []
    lignes = analyser(texte)
    for l in lignes:
        for debut, fin, message in l.erreurs:
            marqueurs.append(Marqueur(ligne=l.numero, colonne_debut=debut, colonne_fin=fin, gravite="erreur",
                                      message=message))
        for s in l.sous_zones:
            if s.code == CODE_PPN and s.valeur and not RE_PPN.fullmatch(s.valeur):
                marqueurs.append(Marqueur(ligne=l.numero, colonne_debut=s.colonne,
                                          colonne_fin=s.colonne + 2 + len(s.valeur), gravite="erreur",
                                          message=f"PPN « {s.valeur} » mal formé (8 chiffres + chiffre ou X)"))
    if not lignes:
        marqueurs.append(Marqueur(ligne=1, gravite="erreur", message="notice vide"))
    p = profil_notice(regles, profil) if regles is not None and profil else None
    if p is None:
        return marqueurs

    presentes = {l.etiquette for l in lignes}
    for tag, regles in sorted(p.prioritaires.items()):
        if tag not in presentes:
            marqueurs.append(Marqueur(ligne=1, gravite="erreur",
                                      message=f"zone {tag} absente (priorité 1 : {', '.join(regles)})"))
    for l in lignes:
        if l.erreurs or not re.fullmatch(r"\d{3}", l.etiquette):
            continue
        if l.etiquette not in p.etiquettes:
            marqueurs.append(Marqueur(ligne=l.numero, colonne_fin=4, gravite="avertissement",
                                      message=f"zone {l.etiquette} produite par aucune ligne du profil {profil}"))
            continue
        connus = p.sous_zones.get(l.etiquette, set())
        for s in l.sous_zones:
            if connus and s.code not in connus:
                marqueurs.append(Marqueur(ligne=l.numero, colonne_debut=s.colonne, colonne_fin=s.colonne + 2,
                                          gravite="avertissement",
                                          message=f"sous-zone {l.etiquette}${s.code} produite par aucune ligne du profil"))
    return marqueurs
