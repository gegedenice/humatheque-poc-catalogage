"""Issue d'un document ou d'une étape (SPEC §7) : ce que le catalogueur doit en faire.

- `terminé` : rien à faire (notice produite ou convertie, ou arrêt sur une
  notice déjà au catalogue) ;
- `à reprendre` : un catalogueur doit trancher (onglet « Reprise » du dossier) ;
- `incident` : panne technique (service injoignable, réponse illisible…).

L'issue d'un dossier est la plus sévère de ses étapes. Les dossiers enregistrés
avant le 06/11 portent l'ancien vocabulaire du routage, traduit à la lecture.
"""

from __future__ import annotations

TERMINE = "terminé"
A_REPRENDRE = "à reprendre"
INCIDENT = "incident"

# Sévérité décroissante.
ORDRE = [INCIDENT, A_REPRENDRE, TERMINE]

# Ancien vocabulaire (routage) → issue.
ANCIENNES = {"auto": TERMINE, "sortie": TERMINE, "conversion": TERMINE, "décision": A_REPRENDRE}


def normaliser(valeur: str | None) -> str | None:
    return ANCIENNES.get(valeur, valeur) if valeur else valeur


def la_plus_severe(issues) -> str:
    presentes = {normaliser(i) for i in issues if i}
    inconnues = presentes - set(ORDRE)
    if inconnues:
        raise ValueError(f"issue(s) inconnue(s) : {', '.join(sorted(inconnues))}")
    return next((i for i in ORDRE if i in presentes), TERMINE)
