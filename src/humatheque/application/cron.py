"""Expressions cron à 5 champs (minute heure jour mois jour-de-semaine), pour le
traitement planifié (SPEC §3.1, ING-F-004). Syntaxe : `*`, valeurs, listes
`1,15`, plages `1-5`, pas `*/15` ou `8-18/2` ; jour de semaine 0-6 (0 =
dimanche, 7 accepté pour dimanche). Heure locale du serveur."""

from __future__ import annotations

from datetime import datetime, timedelta

BORNES = [(0, 59), (0, 23), (1, 31), (1, 12), (0, 7)]
NOMS = ["minute", "heure", "jour", "mois", "jour de semaine"]


def _champ(texte: str, bas: int, haut: int, nom: str) -> set[int]:
    valeurs: set[int] = set()
    for morceau in texte.split(","):
        pas = 1
        if "/" in morceau:
            morceau, p = morceau.split("/", 1)
            if not p.isdigit() or int(p) < 1:
                raise ValueError(f"{nom} : pas « {p} » invalide")
            pas = int(p)
        if morceau == "*":
            debut, fin = bas, haut
        elif "-" in morceau:
            a, b = morceau.split("-", 1)
            if not (a.isdigit() and b.isdigit()):
                raise ValueError(f"{nom} : plage « {morceau} » invalide")
            debut, fin = int(a), int(b)
        elif morceau.isdigit():
            debut = fin = int(morceau)
        else:
            raise ValueError(f"{nom} : « {morceau} » invalide")
        if not (bas <= debut <= fin <= haut):
            raise ValueError(f"{nom} : « {morceau} » hors de {bas}-{haut}")
        valeurs.update(range(debut, fin + 1, pas))
    return valeurs


def analyser(expression: str) -> list[set[int]]:
    champs = (expression or "").split()
    if len(champs) != 5:
        raise ValueError("5 champs attendus : minute heure jour mois jour-de-semaine")
    ensembles = [_champ(c, b, h, n) for c, (b, h), n in zip(champs, BORNES, NOMS)]
    if 7 in ensembles[4]:
        ensembles[4] = (ensembles[4] - {7}) | {0}
    return ensembles


def correspond(expression: str, instant: datetime) -> bool:
    minute, heure, jour, mois, semaine = analyser(expression)
    jour_semaine = (instant.weekday() + 1) % 7  # lundi = 1 … dimanche = 0
    return (instant.minute in minute and instant.hour in heure and instant.month in mois
            and instant.day in jour and jour_semaine in semaine)


def prochaine(expression: str, apres: datetime, horizon_jours: int = 366) -> datetime | None:
    """Prochaine minute (strictement après `apres`) qui correspond, ou None."""
    analyser(expression)
    t = apres.replace(second=0, microsecond=0) + timedelta(minutes=1)
    fin = t + timedelta(days=horizon_jours)
    while t < fin:
        if correspond(expression, t):
            return t
        t += timedelta(minutes=1)
    return None
