"""Sérialisation de la notice : texte WinIBW (format de référence) et MARCXML (SPEC §8)."""

from __future__ import annotations

from pymarc import Field, Record, Subfield, record_to_xml

from humatheque.generateur.modeles import Notice


def vers_winibw(notice: Notice, annoter: bool = False) -> str:
    """Une zone par ligne, triée par étiquette ; `annoter` ajoute règles et statuts."""
    lignes = []
    for z in notice.zones_triees():
        ligne = z.texte()
        if annoter:
            regles = ", ".join(f"{r.id} [{r.statut}]" for r in z.regles)
            ligne += f"    ← {regles}" + (f" ({z.libelle})" if z.libelle else "")
        lignes.append(ligne)
    return "\n".join(lignes)


def vers_marcxml(notice: Notice) -> str:
    rec = Record(force_utf8=True)
    for z in notice.zones_triees():
        if z.controle:
            rec.add_field(Field(tag=z.etiquette, data="".join(s.valeur for s in z.sous_zones)))
        else:
            rec.add_field(Field(
                tag=z.etiquette,
                indicators=[" " if c == "#" else c for c in (z.indicateurs or "##")],
                subfields=[Subfield(s.code, s.valeur) for s in z.sous_zones],
            ))
    return record_to_xml(rec, namespace=True).decode("utf-8")
