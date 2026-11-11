"""Découpage nom / prénom des personnes (SPEC §4.3), pour les zones d'autorité
($a nom, $b prénom).

Décision du 11/11 : la casse, les accents et le retrait des civilités sont faits
par le VLM (prompt d'extraction) ; ici, seulement le découpage, sans changer
l'écriture des mots :
- civilités éventuellement restées en tête : retirées (liste en configuration) ;
- un bloc de mots en capitales (« Jean-Daniel DUBOIS ») est le nom ; sinon le
  dernier mot ;
- particules (`de`, `d'`, `von` rejetées après le prénom ; `La`, `Du`… gardées
  en tête du nom) : forme d'accès française (« La Roche, Marie-France de »).
"""

from __future__ import annotations

import re

from humatheque.texte_unimarc.resultat import Personne
from humatheque.texte_unimarc.ressources import configuration

RE_INITIALE = re.compile(r"^(?:[^\W\d_]\.)(?:-?[^\W\d_]\.)*$")
RE_ELISION = re.compile(r"^([dD])['’](.+)$")


def _capitales(mot: str) -> bool:
    lettres = [c for c in mot if c.isalpha()]
    return len(lettres) >= 2 and not any(c.islower() for c in lettres)


def _norm(mot: str) -> str:
    return mot.lower().replace("’", "'")


def personne(brut: str) -> Personne:
    cfg = configuration()
    civilites = {_norm(c) for c in cfg["civilites"]}
    liaisons = {_norm(m) for m in cfg.get("liaisons_civilites") or []}
    rejetees = {_norm(p) for p in cfg["particules"]["rejetees"]}
    particules = rejetees | {_norm(p) for p in cfg["particules"]["conservees"]}
    trace: list[str] = []

    mots = (brut or "").split()
    while mots and (_norm(mots[0]) in civilites
                    or (len(mots) > 1 and _norm(mots[0]) in liaisons and _norm(mots[1]) in civilites)):
        trace.append(f"civilité retirée : {mots.pop(0)}")
    forme = " ".join(mots)
    if not mots:
        return Personne(forme="", forme_imprimee="", nom="", prenom="", trace=trace)

    jetons: list[str] = []  # « d'Ormesson » → « d' » + « Ormesson »
    for m in mots:
        e = RE_ELISION.match(m)
        jetons += [e[1] + "'", e[2]] if e and len(e[2]) > 1 else [m]

    pleins = [i for i, j in enumerate(jetons) if not RE_INITIALE.match(j) and _norm(j) not in particules]
    caps = [i for i in pleins if _capitales(jetons[i])]
    if caps and len(caps) < len(pleins):  # bloc en capitales = nom
        fin = debut = caps[-1]
        while debut - 1 >= 0 and _capitales(jetons[debut - 1]) and not RE_INITIALE.match(jetons[debut - 1]):
            debut -= 1
        trace.append("nom = mots en capitales")
    else:
        fin = debut = pleins[-1] if pleins else len(jetons) - 1
        trace.append("nom = dernier mot")
    while debut - 1 >= 0 and _norm(jetons[debut - 1]) in particules:
        debut -= 1
    groupe, avant, apres = jetons[debut:fin + 1], jetons[:debut], jetons[fin + 1:]
    rejet: list[str] = []
    while len(groupe) > 1 and _norm(groupe[0]) in rejetees:
        rejet.append(groupe.pop(0))

    def prenom_mot(j: str) -> str:
        return _norm(j) if _norm(j) in particules else j

    nom = " ".join(groupe).replace("' ", "'")
    prenom = " ".join(prenom_mot(j) for j in avant + apres + rejet).replace("' ", "'")
    return Personne(forme=forme, forme_imprimee=forme, nom=nom, prenom=prenom, trace=trace)
