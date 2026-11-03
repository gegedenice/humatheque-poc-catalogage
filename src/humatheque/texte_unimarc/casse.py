"""Réaccentuation et casse des chaînes en capitales (SPEC §4.1, §4.2).

Seul le dictionnaire de reaccentue est utilisé ; la tokenisation et le choix
des formes sont faits ici :
- un seul candidat (minuscules, dédoublonnés, alphabet latin) : on le prend ;
- plusieurs : bigramme avec le mot précédent (sans mot précédent : fréquence
  totale de la forme) ; égalité ou absence de données : forme sans accent +
  alerte `accent_ambigu:<MOT>` ;
- noms propres : majuscule conservée si le mot n'existe qu'avec une majuscule ;
  s'il existe aussi en minuscule, minuscule + `nom_propre_possible:<MOT>`,
  sauf s'il figure parmi les noms propres connus du document ; les noms
  propres de moins de 3 lettres sont ignorés.

Compléments (non prévus par la spec, signalés par alerte ou trace) :
- chiffres romains (`XIXE` → `XIXe`, `LOUIS XIV`) laissés en capitales ;
- mot absent du dictionnaire (sigle, terme rare) : minuscules + `mot_inconnu:<MOT>`.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable

from unidecode import unidecode

from humatheque.texte_unimarc._vendor.reaccentue.dictionnaire import Dictionnaire, cle
from humatheque.texte_unimarc.resultat import Resultat
from humatheque.texte_unimarc.ressources import configuration, dictionnaire

LETTRES = r"[^\W\d_]+"
RE_TOKEN = re.compile(rf"(?P<mot>{LETTRES}(?:-{LETTRES})*)|(?P<apos>['’])|(?P<espace>\s+)|(?P<autre>.)", re.S)
RE_MOT = re.compile(rf"{LETTRES}(?:-{LETTRES})*")
RE_ROMAIN = re.compile(
    r"(?=[IVXLCDM])(?P<n>M{0,3}(?:CM|CD|D?C{0,3})(?:XC|XL|L?X{0,3})(?:IX|IV|V?I{0,3}))(?P<suffixe>E|ER|ERE|EME)?"
)
ROMAINS_ISOLES = {"I", "V", "X"}
# Les noms propres plus courts (symboles chimiques Ce, La…) sont ignorés (décision du 03/11).
LONGUEUR_MIN_NOM_PROPRE = 3


def _latin(forme: str) -> bool:
    return all(not c.isalpha() or unicodedata.name(c, "").startswith("LATIN") for c in forme)


def _candidats(formes: Iterable[str]) -> list[str]:
    vus: list[str] = []
    for f in formes:
        f = f.lower()
        if _latin(f) and f not in vus:
            vus.append(f)
    return vus


def _en_capitales(segment: str) -> bool:
    return any(c.isalpha() for c in segment) and not any(c.islower() for c in segment)


def cles_noms_connus(noms: Iterable[str]) -> set[str]:
    """Clés (majuscules sans accent) des mots des noms propres du document."""
    cles: set[str] = set()
    for nom in noms:
        for m in RE_MOT.findall(nom or ""):
            cles.add(cle(m))
            cles.update(cle(p) for p in m.split("-"))
    return cles


class _Reaccentueur:
    def __init__(self, d: Dictionnaire, connus: set[str], elisions: set[str]) -> None:
        self.d, self.connus, self.elisions = d, connus, elisions
        self.alertes: list[str] = []
        self.trace: list[str] = []

    def _noter(self, mot: str, forme: str, motif: str) -> None:
        self.trace.append(f"{mot} → {forme} ({motif})")

    def _alerter(self, code: str, mot: str) -> None:
        a = f"{code}:{mot}"
        if a not in self.alertes:
            self.alertes.append(a)

    def _commun(self, mot: str, k: str, prec: str | None) -> str:
        cands = _candidats(self.d.formes.get(k, []))
        if not cands:
            forme = mot.lower()
            if k in self.d.mots_connus:
                self._noter(mot, forme, "sans variante accentuée")
            else:
                self._alerter("mot_inconnu", mot)
                self._noter(mot, forme, "absent du dictionnaire")
            return forme
        if len(cands) == 1:
            self._noter(mot, cands[0], "candidat unique")
            return cands[0]
        if prec:
            scores = {c: self.d.bigrammes.get(f"{prec} {c}", 0) for c in cands}
            motif = "bigramme « " + prec + " {c} » = {n}"
        else:
            # Pas de mot précédent : fréquence totale de la forme (décision du 03/11)
            scores = {c: self.d.frequences.get(c, 0) for c in cands}
            motif = "fréquence totale de « {c} » = {n}, sans mot précédent"
        meilleur = max(scores.values())
        gagnants = [c for c, s in scores.items() if s == meilleur]
        if meilleur > 0 and len(gagnants) == 1:
            self._noter(mot, gagnants[0], motif.format(c=gagnants[0], n=meilleur))
            return gagnants[0]
        forme = unidecode(mot).lower()
        self._alerter("accent_ambigu", mot)
        self._noter(mot, forme, f"ambigu entre {', '.join(cands)}")
        return forme

    def _propre(self, mot: str, k: str, motif: str) -> str:
        cands = list(dict.fromkeys(f for f in self.d.noms_propres[k] if _latin(f)))
        if len(cands) == 1:
            self._noter(mot, cands[0], motif)
            return cands[0]
        forme = unidecode(mot).capitalize()
        self._alerter("accent_ambigu", mot)
        self._noter(mot, forme, f"{motif}, ambigu entre {', '.join(cands)}")
        return forme

    def mot(self, mot: str, prec: str | None) -> str:
        k = cle(mot)
        m = RE_ROMAIN.fullmatch(k)
        if m and (k not in self.d.mots_connus or k in ROMAINS_ISOLES):
            forme = m["n"] + (m["suffixe"] or "").lower()
            self._noter(mot, forme, "chiffre romain")
            return forme
        if "-" in mot and k not in self.d.formes and k not in self.d.noms_propres:
            parties = mot.split("-")
            return "-".join(self.mot(p, prec if i == 0 else None) for i, p in enumerate(parties))
        if len(k) > LONGUEUR_MIN_NOM_PROPRE - 1 and k in self.d.noms_propres:
            if k not in self.d.noms_propres_ambigus:
                return self._propre(mot, k, "nom propre")
            if k in self.connus:
                return self._propre(mot, k, "nom propre connu du document")
            self._alerter("nom_propre_possible", mot)
        return self._commun(mot, k, prec)

    def segment(self, texte: str) -> str:
        sortie: list[str] = []
        prec: str | None = None
        jetons = list(RE_TOKEN.finditer(texte))
        for i, t in enumerate(jetons):
            if t["mot"]:
                suivant = jetons[i + 1] if i + 1 < len(jetons) else None
                if suivant is not None and suivant["apos"] and cle(t["mot"]) in self.elisions:
                    forme = t["mot"].lower()
                    self._noter(t["mot"], forme, "élision")
                    sortie.append(forme)
                    prec = None
                    continue
                forme = self.mot(t["mot"], prec)
                sortie.append(forme)
                prec = forme.lower()
            elif t["espace"]:
                sortie.append(t[0])
            else:
                sortie.append(t[0])
                prec = None
        return "".join(sortie)


def _reaccentuer(texte: str, noms_connus: Iterable[str], d: Dictionnaire | None) -> tuple[Resultat, bool]:
    cfg = configuration()
    r = _Reaccentueur(d or dictionnaire(), cles_noms_connus(noms_connus), set(cfg["elisions"]))
    seps = re.escape(cfg["separateurs_segments"])
    morceaux = re.split(f"([{seps}])", texte or "")
    premier_traite = None
    for i, morceau in enumerate(morceaux):
        if i % 2 == 0 and _en_capitales(morceau):
            morceaux[i] = r.segment(morceau)
            premier_traite = True if premier_traite is None else premier_traite
        elif any(c.isalpha() for c in morceau) and premier_traite is None:
            premier_traite = False
    return Resultat(valeur="".join(morceaux), alertes=r.alertes, trace=r.trace), bool(premier_traite)


def reaccentuer(texte: str, noms_connus: Iterable[str] = (), d: Dictionnaire | None = None) -> Resultat:
    """Réaccentue les segments en capitales (minuscules sauf noms propres)."""
    return _reaccentuer(texte, noms_connus, d)[0]


def _majuscule_initiale(texte: str) -> str:
    for i, c in enumerate(texte):
        if c.isalpha():
            return texte[:i] + c.upper() + texte[i + 1:]
    return texte


def casse_phrase(texte: str, noms_connus: Iterable[str] = (), d: Dictionnaire | None = None) -> Resultat:
    """Réaccentuation, minuscules sauf noms propres, majuscule au premier mot (200$a, 328$c, 328$e)."""
    r, premier_traite = _reaccentuer(texte, noms_connus, d)
    if premier_traite:
        r.valeur = _majuscule_initiale(r.valeur)
    return r


def casse_complement_titre(texte: str, noms_connus: Iterable[str] = (), d: Dictionnaire | None = None) -> Resultat:
    """Comme `casse_phrase`, premier mot en minuscule sauf nom propre (200$e)."""
    return reaccentuer(texte, noms_connus, d)
