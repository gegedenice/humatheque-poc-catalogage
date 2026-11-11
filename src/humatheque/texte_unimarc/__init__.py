"""Mise en forme des chaînes pour UNIMARC (SPEC §4), réduite au strict nécessaire
(décision du 11/11) : la casse, les accents et les civilités sont pris en charge
par le VLM (prompt d'extraction). Restent ici :

- `titre_classement` : `@` devant le premier mot significatif du titre ;
- `minuscule_initiale` : première lettre du sous-titre en minuscule ;
- `personne` : découpage nom / prénom pour les zones d'autorité.
"""

from humatheque.texte_unimarc.casse import minuscule_initiale
from humatheque.texte_unimarc.personnes import personne
from humatheque.texte_unimarc.resultat import Personne, Resultat
from humatheque.texte_unimarc.titre import titre_classement

__all__ = ["Personne", "Resultat", "minuscule_initiale", "personne", "titre_classement"]
