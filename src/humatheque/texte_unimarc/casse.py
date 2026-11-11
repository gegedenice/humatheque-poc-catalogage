"""Sous-titre (SPEC §4.2) : première lettre en minuscule (200 $e), sauf sigle ou
nombre romain en capitales (« CNRS », « XIXe »). Le reste est laissé tel que le
VLM l'a écrit (décision du 11/11 : casse et accents par le prompt)."""

from __future__ import annotations

from humatheque.texte_unimarc.resultat import Resultat


def minuscule_initiale(texte: str) -> Resultat:
    texte = (texte or "").strip()
    if not texte:
        return Resultat(valeur="")
    premier = texte.split()[0]
    lettres = [c for c in premier if c.isalpha()]
    if len(lettres) >= 2 and sum(c.isupper() for c in lettres) >= 2:
        return Resultat(valeur=texte, trace=[f"« {premier} » : sigle ou capitales, inchangé"])
    if not texte[0].isupper():
        return Resultat(valeur=texte)
    return Resultat(valeur=texte[0].lower() + texte[1:], trace=["première lettre en minuscule"])
