"""Découpage nom / prénom des personnes (SPEC §4.3).

1. retrait des civilités (liste en configuration) ;
2. séparation nom / prénom avant toute conversion de casse :
   - une partie seulement des mots en capitales : ces mots forment le nom ;
   - tout en capitales ou tout en casse mixte : le dernier mot est le nom
     (alerte `decoupage_heuristique`) ;
3. particules : forme d'accès française, les particules rejetées (`de`, `d'`)
   passent après le prénom, les articles (`La`, `Du`…) restent en tête du nom
   (alerte `particule_a_verifier`) ;
4. casse : majuscule initiale à chaque élément, y compris après un trait
   d'union. Un mot imprimé en capitales sans accent reçoit l'accent si le
   dictionnaire n'en connaît qu'une forme de nom propre, accentuée (alerte
   `accent_restitue:<MOT>`) ; sinon, s'il existe avec accent, alerte
   `accent_possible:<MOT>` (décision du 05/11) ;
5. forme retraitée « Prénom Nom » (200$f, 200$g) ; la forme imprimée,
   civilités retirées, est conservée à part.

Les initiales (`J.`, `J.-D.`) ne comptent ni comme capitales ni comme casse mixte.
"""

from __future__ import annotations

import re

from unidecode import unidecode

from humatheque.texte_unimarc._vendor.reaccentue.dictionnaire import Dictionnaire, cle
from humatheque.texte_unimarc.resultat import Personne
from humatheque.texte_unimarc.ressources import configuration, dictionnaire

RE_INITIALE = re.compile(r"^(?:[^\W\d_]\.)(?:-?[^\W\d_]\.)*$")
RE_ELISION = re.compile(r"^([dD])['’](.+)$")
RE_LETTRES = re.compile(r"[^\W\d_]+")


def _capitales(mot: str) -> bool:
    lettres = [c for c in mot if c.isalpha()]
    return len(lettres) >= 2 and not any(c.islower() for c in lettres)


def _casse_mixte(mot: str) -> bool:
    return any(c.islower() for c in mot)


def _majuscules_initiales(mot: str) -> str:
    return RE_LETTRES.sub(lambda m: m[0][:1].upper() + m[0][1:].lower(), mot)


def _norm(mot: str) -> str:
    return mot.lower().replace("’", "'")


def personne(brut: str, d: Dictionnaire | None = None) -> Personne:
    cfg = configuration()
    civilites = {_norm(c) for c in cfg["civilites"]}
    rejetees = {_norm(p) for p in cfg["particules"]["rejetees"]}
    particules = rejetees | {_norm(p) for p in cfg["particules"]["conservees"]}
    alertes: list[str] = []
    trace: list[str] = []

    liaisons = {_norm(m) for m in cfg.get("liaisons_civilites") or []}
    mots = (brut or "").split()
    while mots and (_norm(mots[0]) in civilites
                    or (len(mots) > 1 and _norm(mots[0]) in liaisons and _norm(mots[1]) in civilites)):
        trace.append(f"civilité retirée : {mots.pop(0)}")
    forme = " ".join(mots)
    if not mots:
        return Personne(forme="", forme_imprimee="", nom="", prenom="", alertes=["personne_vide"], trace=trace)

    # « D'ORMESSON » → « D' » + « ORMESSON » (particule élidée)
    jetons: list[str] = []
    for m in mots:
        e = RE_ELISION.match(m)
        if e and len(e[2]) > 1:
            jetons += [e[1] + "'", e[2]]
        else:
            jetons.append(m)

    pleins = [i for i, j in enumerate(jetons) if not RE_INITIALE.match(j) and _norm(j) not in particules]
    caps = [i for i in pleins if _capitales(jetons[i])]
    mixtes = [i for i in pleins if _casse_mixte(jetons[i])]
    initiales = [i for i, j in enumerate(jetons) if RE_INITIALE.match(j)]

    if caps and (mixtes or initiales):
        # Dernier bloc contigu de mots en capitales (particules en capitales comprises)
        fin = caps[-1]
        debut = fin
        while debut - 1 >= 0 and (_capitales(jetons[debut - 1]) and not RE_INITIALE.match(jetons[debut - 1])):
            debut -= 1
        if any(i < debut for i in caps):
            alertes.append("decoupage_heuristique")
            trace.append("plusieurs blocs en capitales : dernier bloc retenu comme nom")
        trace.append("nom = mots en capitales")
    else:
        fin = pleins[-1] if pleins else len(jetons) - 1
        debut = fin
        alertes.append("decoupage_heuristique")
        trace.append("nom = dernier mot (casse uniforme)")

    # Particules qui précèdent le nom
    while debut - 1 >= 0 and _norm(jetons[debut - 1]) in particules:
        debut -= 1
    groupe = jetons[debut:fin + 1]
    avant, apres = jetons[:debut], jetons[fin + 1:]

    rejet: list[str] = []
    while len(groupe) > 1 and _norm(groupe[0]) in rejetees:
        rejet.append(groupe.pop(0))
    if any(_norm(j) in particules for j in jetons[debut:fin + 1]):
        alertes.append("particule_a_verifier")
        trace.append(f"particule(s) : {' '.join(jetons[debut:fin + 1])} → nom « {' '.join(groupe)} », rejet « {' '.join(rejet)} »")

    d = d or dictionnaire()

    def casse(j: str) -> str:
        """Majuscule initiale à chaque élément ; accent restitué pour un mot imprimé en
        capitales sans accent si le dictionnaire n'en connaît qu'une forme, accentuée
        (décision du 05/11), sinon alerte accent_possible."""
        forme_j = _majuscules_initiales(j)
        if not _capitales(j) or _norm(j) in particules or j != unidecode(j):
            return forme_j
        parties = []
        for partie in forme_j.split("-"):
            unique = _accent_unique(partie, d)
            if unique:
                alertes.append(f"accent_restitue:{partie.upper()}")
                trace.append(f"accent restitué : {partie.upper()} → {unique}")
                parties.append(unique)
            else:
                if _accentuable(partie, d):
                    alertes.append(f"accent_possible:{partie.upper()}")
                parties.append(partie)
        return "-".join(parties)

    def mot_prenom(j: str) -> str:
        return _norm(j) if _norm(j) in particules else casse(j)

    nom = " ".join(casse(j) for j in groupe).replace("' ", "'")
    prenom = " ".join(mot_prenom(j) for j in avant + apres + rejet)
    # Forme retraitée, dans l'ordre de lecture (décision du 05/11 : 200$f, 200$g)
    forme_retraitee = f"{prenom} {nom}".strip().replace("' ", "'") if prenom else nom
    return Personne(forme=forme_retraitee, forme_imprimee=forme, nom=nom, prenom=prenom, alertes=alertes,
                    trace=trace)


def _accent_unique(mot: str, d: Dictionnaire) -> str | None:
    """Unique forme (accentuée) d'un nom propre sans accent, sinon None."""
    if not mot or mot != unidecode(mot):
        return None
    formes = list(dict.fromkeys(f for f in d.noms_propres.get(cle(mot), []) if f[:1].isupper()))
    if len(formes) == 1 and formes[0] != unidecode(formes[0]) and cle(mot) not in d.noms_propres_ambigus:
        return formes[0]
    return None


def _accentuable(mot: str, d: Dictionnaire) -> bool:
    """Le mot (sans accent) existe-t-il avec accent dans le dictionnaire ?"""
    if mot != unidecode(mot):
        return False
    k = cle(mot)
    formes = list(d.noms_propres.get(k, [])) + list(d.formes.get(k, []))
    return any(f != unidecode(f) for f in formes)
