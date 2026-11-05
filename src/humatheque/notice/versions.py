"""Versions de la notice d'un dossier (SPEC §9.2).

- la notice générée (ou convertie) est conservée telle quelle (version 1) ;
- chaque enregistrement crée une version (auteur, date, différence avec la
  précédente) ; statuts : générée → éditée → validée (action explicite,
  refusée s'il reste des erreurs) ;
- une régénération alors qu'une version éditée ou validée existe n'écrase
  pas l'édition : elle devient une version « proposée », que le catalogueur
  reporte (nouvelle version) ou ignore.
"""

from __future__ import annotations

import difflib
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field

Source = Literal["générée", "convertie", "édition", "report"]
Statut = Literal["générée", "éditée", "validée", "proposée", "reportée", "ignorée"]
HORS_COURANT = ("proposée", "reportée", "ignorée")


class VersionNotice(BaseModel):
    dossier_id: str
    numero: int
    le: datetime = Field(default_factory=lambda: datetime.now(UTC))
    auteur: str
    source: Source
    statut: Statut
    texte: str
    diff: str = ""


class ConflitVersion(Exception):
    """La version de base n'est plus la version courante."""


class ValidationRefusee(Exception):
    def __init__(self, erreurs: list) -> None:
        super().__init__(f"{len(erreurs)} erreur(s) : validation refusée")
        self.erreurs = erreurs


def difference(avant: str, apres: str) -> str:
    return "\n".join(difflib.unified_diff(avant.splitlines(), apres.splitlines(), "précédente", "nouvelle",
                                          lineterm="", n=1))


def courante(versions: list[VersionNotice]) -> VersionNotice | None:
    return next((v for v in reversed(versions) if v.statut not in HORS_COURANT), None)


def proposition(versions: list[VersionNotice]) -> VersionNotice | None:
    c = courante(versions)
    return next((v for v in reversed(versions) if v.statut == "proposée" and (c is None or v.numero > c.numero)), None)


def _nouvelle(versions, dossier_id, auteur, source, statut, texte, base: str) -> VersionNotice:
    return VersionNotice(dossier_id=dossier_id, numero=(versions[-1].numero + 1) if versions else 1, auteur=auteur,
                         source=source, statut=statut, texte=texte, diff=difference(base, texte))


def enregistrer_production(versions: list[VersionNotice], dossier_id: str, texte: str,
                           source: Literal["générée", "convertie"]) -> VersionNotice | None:
    """Notice produite par le pipeline ; None si rien de nouveau."""
    c = courante(versions)
    if c is None:
        return _nouvelle(versions, dossier_id, "système", source, "générée", texte, "")
    if c.texte == texte:
        return None
    if c.statut == "générée":
        return _nouvelle(versions, dossier_id, "système", source, "générée", texte, c.texte)
    p = proposition(versions)
    if p is not None and p.texte == texte:
        return None
    return _nouvelle(versions, dossier_id, "système", source, "proposée", texte, c.texte)


def editer(versions: list[VersionNotice], dossier_id: str, texte: str, auteur: str, base: int) -> VersionNotice:
    c = courante(versions)
    if c is None:
        raise ConflitVersion("aucune notice à éditer")
    if c.numero != base:
        raise ConflitVersion(f"la notice a changé (version courante {c.numero}, éditée depuis {base})")
    return _nouvelle(versions, dossier_id, auteur, "édition", "éditée", texte, c.texte)


def valider(versions: list[VersionNotice], dossier_id: str, auteur: str, erreurs: list) -> VersionNotice:
    c = courante(versions)
    if c is None:
        raise ConflitVersion("aucune notice à valider")
    if erreurs:
        raise ValidationRefusee(erreurs)
    return _nouvelle(versions, dossier_id, auteur, c.source if c.source != "report" else "édition", "validée",
                     c.texte, c.texte)


def reporter(versions: list[VersionNotice], dossier_id: str, numero: int, auteur: str) -> VersionNotice:
    p = proposition(versions)
    c = courante(versions)
    if p is None or p.numero != numero or c is None:
        raise ConflitVersion("aucune proposition de régénération en attente")
    return _nouvelle(versions, dossier_id, auteur, "report", "éditée", p.texte, c.texte)


def a_exporter(versions: list[VersionNotice]) -> VersionNotice | None:
    """Dernière version validée, à défaut la version générée (ou convertie) (SPEC §8)."""
    validees = [v for v in versions if v.statut == "validée"]
    if validees:
        return validees[-1]
    return next((v for v in reversed(versions) if v.source in ("générée", "convertie")
                 and v.statut == "générée"), None)
