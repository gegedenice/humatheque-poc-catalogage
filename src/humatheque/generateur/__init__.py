"""Générateur UNIMARC piloté par la notice structurée de regles/notice/ (SPEC §8).

`moteur` est importé à la demande : `regles.notice` utilise les modèles de
notice de ce package, et `generateur.moteur` utilise `regles.notice`.
"""

from humatheque.generateur.modeles import Notice, Zone
from humatheque.generateur.serialisation import vers_marcxml, vers_winibw

__all__ = ["GenerationEchec", "Generateur", "Notice", "Zone", "generer", "vers_marcxml", "vers_winibw"]


def __getattr__(nom: str):
    if nom in ("GenerationEchec", "Generateur", "generer"):
        from humatheque.generateur import moteur

        return getattr(moteur, nom)
    raise AttributeError(nom)
