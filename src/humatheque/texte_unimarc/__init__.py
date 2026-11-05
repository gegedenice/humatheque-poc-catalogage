"""Normalisation des chaînes pour UNIMARC (« module NOR » du wiki, SPEC §4).

Fonctions pures, sans réseau ; chacune renvoie un `Resultat` (ou une
`Personne`) avec alertes et trace.
"""

from humatheque.texte_unimarc.casse import casse_complement_titre, casse_phrase, reaccentuer
from humatheque.texte_unimarc.personnes import personne
from humatheque.texte_unimarc.resultat import Personne, Resultat
from humatheque.texte_unimarc.titre import titre_classement

__all__ = [
    "Personne", "Resultat", "casse_complement_titre", "casse_phrase", "personne",
    "reaccentuer", "titre_classement",
]
