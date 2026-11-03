"""Conversion d'une notice Sudoc existante (SPEC §7).

La notice source est lue en UNIMARC XML (`https://www.sudoc.fr/{ppn}.xml`),
convertie en ISO 2709 avec pymarc ; c'est l'ISO qui est conservé
(ALM-SUD-F-002) et passé à `convertir`. Un rendu WinIBW en est dérivé pour
l'affichage.

En v1, les règles de conversion ne sont pas définies : `convertir` renvoie
une copie de la notice source, avec l'alerte `conversion_non_implementee`.
Cette fonction sera remplacée sans toucher au workflow.

Note : pymarc écrit l'ISO en UTF-8 et positionne alors le caractère 9 du
label à `a` (position non définie en UNIMARC) ; le contenu des zones est
inchangé.
"""

from __future__ import annotations

import io

import pymarc
from pydantic import BaseModel, Field


class ConversionEchec(Exception):
    """Notice source illisible ou introuvable (statut `conversion_echec`)."""


class NoticeConvertie(BaseModel):
    iso: str = Field(description="notice ISO 2709 (UTF-8)")
    texte: str = Field(description="rendu WinIBW, pour affichage et édition")
    profil_cible: str | None
    alertes: list[str] = Field(default_factory=list)


def lire_unimarc_xml(xml: str | bytes) -> pymarc.Record:
    donnees = xml.encode("utf-8") if isinstance(xml, str) else xml
    try:
        notices = pymarc.parse_xml_to_array(io.BytesIO(donnees), strict=False)
    except Exception as e:  # SAX lève plusieurs types d'erreurs
        raise ConversionEchec(f"notice source illisible : {e}") from e
    notices = [n for n in notices if n is not None and n.fields]
    if not notices:
        raise ConversionEchec("notice source vide ou sans élément record")
    return notices[0]


def vers_iso(notice: pymarc.Record) -> str:
    notice.force_utf8 = True
    return notice.as_marc().decode("utf-8")


def depuis_iso(iso: str) -> pymarc.Record:
    try:
        notice = next(pymarc.MARCReader(iso.encode("utf-8"), to_unicode=True, force_utf8=True))
    except (StopIteration, pymarc.exceptions.PymarcException) as e:
        raise ConversionEchec(f"notice ISO illisible : {e}") from e
    if notice is None:
        raise ConversionEchec("notice ISO illisible")
    return notice


def vers_winibw(notice: pymarc.Record) -> str:
    """Une zone par ligne, triée par étiquette : `ZZZ II$a…` ; zones de contrôle `ZZZ valeur`."""
    lignes = []
    for champ in notice.fields:
        if champ.is_control_field():
            lignes.append((champ.tag, f"{champ.tag} {champ.data}"))
        else:
            ind = "".join((i or " ").replace(" ", "#") for i in champ.indicators)
            sz = "".join(f"${s.code}{s.value}" for s in champ.subfields)
            lignes.append((champ.tag, f"{champ.tag} {ind}{sz}"))
    return "\n".join(l for _, l in sorted(lignes, key=lambda x: x[0]))


def unimarc_xml_vers_iso(xml: str | bytes) -> str:
    return vers_iso(lire_unimarc_xml(xml))


def convertir(iso_source: str, profil_cible: str | None) -> NoticeConvertie:
    """v1 : copie de la notice source."""
    return NoticeConvertie(iso=iso_source, texte=vers_winibw(depuis_iso(iso_source)),
                           profil_cible=profil_cible, alertes=["conversion_non_implementee"])
