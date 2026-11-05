"""Analyse stricte des colonnes `gabarit` et `repli` de `Regles` (SPEC §8).

Syntaxe :
- `ZZZ II$x…` ouvre une occurrence de la zone ZZZ, indicateurs II (`#` = blanc) ;
- `ZZZ $x…` (sans indicateurs) : zone de contrôle ;
- `$x…` : fragment ajouté à la zone de la colonne `zone` ;
- `{var}` : variable du contexte ;
- interdits : `|`, `…`, `?`, `{?}`, sous-zone sans valeur.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

RE_OUVERTURE = re.compile(r"^(?P<etiquette>\d{3}) (?P<indicateurs>[0-9#]{2})?(?=\$)")
RE_SOUS_ZONE = re.compile(r"\$(?P<code>[0-9A-Za-z])(?P<valeur>[^$]*)")
RE_VARIABLE = re.compile(r"\{(\w+)\}")
INTERDITS = ("|", "…", "?")


class GabaritInvalide(ValueError):
    """Gabarit non analysable ; le message dit pourquoi."""


@dataclass(frozen=True)
class Gabarit:
    texte: str
    etiquette: str | None  # None : fragment rattaché à la zone de la règle
    indicateurs: str | None  # None : non déclarés (fragment) ou zone de contrôle
    sous_zones: list[tuple[str, str]] = field(default_factory=list)

    @property
    def ouvre_zone(self) -> bool:
        return self.etiquette is not None

    @property
    def variables(self) -> list[str]:
        return [v for _, valeur in self.sous_zones for v in RE_VARIABLE.findall(valeur)]


def analyser(texte: str) -> Gabarit:
    """Analyse un gabarit ou un repli ; lève `GabaritInvalide`."""
    if not texte or not texte.strip():
        raise GabaritInvalide("gabarit vide")
    if texte != texte.strip():
        raise GabaritInvalide("espaces en début ou fin de gabarit")
    for c in INTERDITS:
        if c in texte:
            raise GabaritInvalide(f"caractère interdit « {c} »")

    etiquette = indicateurs = None
    reste = texte
    m = RE_OUVERTURE.match(texte)
    if m:
        etiquette, indicateurs = m["etiquette"], m["indicateurs"]
        reste = texte[m.end():]
    elif not texte.startswith("$"):
        raise GabaritInvalide(
            "début attendu : « ZZZ II$x » (ouverture de zone) ou « $x » (fragment)"
        )

    sous_zones: list[tuple[str, str]] = []
    pos = 0
    for sz in RE_SOUS_ZONE.finditer(reste):
        if sz.start() != pos:
            raise GabaritInvalide(f"texte non analysable : « {reste[pos:sz.start()]} »")
        pos = sz.end()
        code, valeur = sz["code"], sz["valeur"]
        if not valeur:
            raise GabaritInvalide(f"sous-zone ${code} sans valeur")
        _verifier_accolades(valeur)
        sous_zones.append((code, valeur))
    if pos != len(reste) or not sous_zones:
        raise GabaritInvalide(f"sous-zone mal formée : « {reste[pos:] or reste} »")
    return Gabarit(texte, etiquette, indicateurs, sous_zones)


def _verifier_accolades(valeur: str) -> None:
    sans_variables = RE_VARIABLE.sub("", valeur)
    if "{" in sans_variables or "}" in sans_variables:
        raise GabaritInvalide(f"variable mal formée dans « {valeur} »")
