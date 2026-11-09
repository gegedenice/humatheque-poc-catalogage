"""Analyse du texte d'une notice au format WinIBW (SPEC §9.2) : une zone par
ligne, `ZZZ II$a…` ; zones de contrôle (étiquette < 010) `ZZZ valeur` ou
`ZZZ $a…`. Lignes vides ignorées."""

from __future__ import annotations

import re

from pydantic import BaseModel, Field
from pymarc import Field as Champ
from pymarc import Record, Subfield, record_to_xml

RE_LIGNE = re.compile(r"^(?P<etiquette>\S+)(?: (?P<reste>.*))?$")
RE_SOUS_ZONE = re.compile(r"\$(?P<code>.)(?P<valeur>[^$]*)")


class SousZoneNotice(BaseModel):
    code: str
    valeur: str
    colonne: int = Field(description="colonne (1-indexée) du $")


class LigneNotice(BaseModel):
    numero: int
    texte: str
    etiquette: str
    indicateurs: str | None = None
    donnees: str | None = Field(None, description="valeur d'une zone de contrôle sans sous-zone")
    sous_zones: list[SousZoneNotice] = Field(default_factory=list)
    erreurs: list[tuple[int, int, str]] = Field(default_factory=list, description="(col. début, col. fin, message)")

    @property
    def controle(self) -> bool:
        return self.etiquette.isdigit() and int(self.etiquette) < 10


def analyser_ligne(numero: int, texte: str) -> LigneNotice:
    m = RE_LIGNE.match(texte)
    etiquette = m["etiquette"] if m else texte
    ligne = LigneNotice(numero=numero, texte=texte, etiquette=etiquette)
    fin = len(texte) + 1
    if not re.fullmatch(r"\d{3}", etiquette):
        ligne.erreurs.append((1, len(etiquette) + 1, f"étiquette « {etiquette} » : 3 chiffres attendus"))
        return ligne
    reste = (m["reste"] if m else None) or ""
    debut_reste = 5  # « ZZZ » + espace
    if ligne.controle:
        if not reste.startswith("$"):
            ligne.donnees = reste
            if not reste.strip():
                ligne.erreurs.append((1, fin, "zone de contrôle vide"))
            return ligne
    else:
        ind = re.match(r"([0-9#]{2})(?=\$)", reste)
        if not ind:
            ligne.erreurs.append((debut_reste, min(debut_reste + 2, fin), "indicateurs attendus (2 caractères parmi 0-9 et #) suivis de $"))
            return ligne
        ligne.indicateurs = ind[1]
        reste, debut_reste = reste[2:], debut_reste + 2
    if not reste.startswith("$"):
        ligne.erreurs.append((debut_reste, fin, "sous-zone attendue : $x…"))
        return ligne
    for sz in RE_SOUS_ZONE.finditer(reste):
        col = debut_reste + sz.start()
        code, valeur = sz["code"], sz["valeur"]
        if not re.fullmatch(r"[0-9A-Za-z]", code):
            ligne.erreurs.append((col, col + 2, f"code de sous-zone « {code} » invalide"))
        elif not valeur:
            ligne.erreurs.append((col, col + 2, f"sous-zone ${code} sans valeur"))
        ligne.sous_zones.append(SousZoneNotice(code=code, valeur=valeur, colonne=col))
    return ligne


def analyser(texte: str) -> list[LigneNotice]:
    return [analyser_ligne(i, l.rstrip("\r")) for i, l in enumerate((texte or "").split("\n"), start=1) if l.strip()]


def vers_record(texte: str) -> Record:
    """Notice WinIBW → pymarc.Record (lignes erronées ignorées)."""
    rec = Record(force_utf8=True)
    for l in analyser(texte):
        if l.erreurs:
            continue
        if l.controle:
            rec.add_field(Champ(tag=l.etiquette, data=l.donnees if l.donnees is not None
                                else "".join(s.valeur for s in l.sous_zones)))
        else:
            rec.add_field(Champ(tag=l.etiquette, indicators=[" " if c == "#" else c for c in l.indicateurs or "##"],
                                subfields=[Subfield(s.code, s.valeur) for s in l.sous_zones]))
    return rec


def record_vers_xml(rec: Record, namespace: bool = True) -> str:
    """Notice → UNIMARC/XML en UTF-8 (caractères accentués lisibles, pas d'entités)."""
    import xml.etree.ElementTree as ET

    from pymarc.marcxml import record_to_xml_node

    return ET.tostring(record_to_xml_node(rec, namespace=namespace), encoding="unicode")


def vers_marcxml(texte: str) -> str:
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + record_vers_xml(vers_record(texte))
