"""Validation d'une notice éditée (SPEC §9.2), affichée en marqueurs Monaco :

- syntaxe : étiquette sur 3 chiffres, indicateurs, sous-zones $x non vides ;
- règles de la spec (profil du dossier) : zones de priorité 1 absentes
  (erreur), zones ou sous-zones produites par aucune règle du profil
  (avertissement), PPN mal formés ($3, erreur).

Les zones et sous-zones connues viennent des gabarits de `Regles` : aucune
liste UNIMARC dans le code.
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Literal

from pydantic import BaseModel

from humatheque.config import reglages
from humatheque.notice.analyse import analyser
from humatheque.spec.gabarit import GabaritInvalide, analyser as analyser_gabarit
from humatheque.spec.modeles import Spec
from humatheque.spec.verificateur import Vocabulaires, etiquettes_zone

RE_PPN = re.compile(r"\d{8}[\dX]")
CODE_PPN = "3"  # sous-zone de lien vers une autorité (PPN) dans les gabarits de Regles


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


def profil_notice(spec: Spec, profil: str, vocab: Vocabulaires | None = None) -> ProfilNotice:
    vocab = vocab or Vocabulaires.depuis_config()
    exclus = set(reglages().regles_statuts_exclus)
    prioritaires: dict[str, list[str]] = defaultdict(list)
    sous_zones: dict[str, set[str]] = defaultdict(set)
    etiquettes: set[str] = set()
    for r in spec.regles:
        if profil not in r.liste_profils(vocab.sep_liste) or r.statut in exclus:
            continue
        tags = etiquettes_zone(r.zone, vocab.sep_plage) or []
        etiquettes.update(tags)
        if "$" in r.zone:
            sous_zones[r.zone[:3]].add(r.zone.split("$", 1)[1])
        for texte in (r.gabarit, r.repli):
            try:
                g = analyser_gabarit(texte) if texte else None
            except GabaritInvalide:
                g = None
            if g:
                for tag in ([g.etiquette] if g.etiquette else tags):
                    sous_zones[tag].update(code for code, _ in g.sous_zones)
        if r.priorite == "1" and r.mode_production != vocab.mode_non_genere:
            for tag in tags:
                prioritaires[tag].append(r.id)
    return ProfilNotice(prioritaires=dict(prioritaires), sous_zones=dict(sous_zones), etiquettes=etiquettes)


def valider(texte: str, spec: Spec | None = None, profil: str | None = None) -> list[Marqueur]:
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
    if spec is None or profil is None:
        return marqueurs

    p = profil_notice(spec, profil)
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
                                      message=f"zone {l.etiquette} produite par aucune règle du profil {profil}"))
            continue
        connus = p.sous_zones.get(l.etiquette, set())
        for s in l.sous_zones:
            if connus and s.code not in connus:
                marqueurs.append(Marqueur(ligne=l.numero, colonne_debut=s.colonne, colonne_fin=s.colonne + 2,
                                          gravite="avertissement",
                                          message=f"sous-zone {l.etiquette}${s.code} produite par aucune règle du profil"))
    return marqueurs
