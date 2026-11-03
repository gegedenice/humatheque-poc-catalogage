"""Construction du dictionnaire de reaccentue (https://github.com/cquest/reaccentue).

Adapté de `reaccentue_origine.py` (copie non modifiée du dépôt, commit
bc48cfd, 12/02/2021) : mêmes règles de construction (`add_dico`, `load_word`,
`load_dico`, `reduce_dico`), mais
- chemins relatifs au module (et non au répertoire courant) ;
- cache dans un chemin fourni par l'appelant (`data/`) ;
- aucune exécution à l'import ;
- ensemble des noms propres relevé AVANT la réduction (SPEC §4.1) ;
- fréquences de bigrammes rangées à part (mêmes clés « mot_précédent mot »).

La fonction `reaccentue()` d'origine n'est PAS reprise (SPEC §4.1).
"""

from __future__ import annotations

import gzip
import pickle
import re
from dataclasses import dataclass
from pathlib import Path

from unidecode import unidecode

DOSSIER_DICO = Path(__file__).resolve().parent / "dico"
VERSION_CACHE = 2


@dataclass
class Dictionnaire:
    # clé = forme majuscule sans accent → formes candidates (après réduction)
    formes: dict[str, list[str]]
    # « mot_précédent mot » (minuscules accentuées) → fréquence
    bigrammes: dict[str, int]
    # clé → formes à majuscule initiale (relevées avant réduction)
    noms_propres: dict[str, list[str]]
    # clés existant aussi avec une forme en minuscule (Rousseau / rousseau)
    noms_propres_ambigus: set[str]
    # toutes les clés connues avant réduction (mots du dictionnaire)
    mots_connus: set[str]


# ------------------------------------------------- règles d'origine (inchangées)
def cle(mot: str) -> str:
    return unidecode(mot).upper()


def add_dico(mot: str, dico: dict[str, list[str]]) -> None:
    if mot < "A":
        return
    maj = cle(mot)
    if maj in dico:
        if mot not in dico[maj]:
            dico[maj].append(mot)
    else:
        dico[maj] = [mot]


def load_word(mot: str, dico: dict, affixes: dict) -> None:
    m = re.sub(r"/.*", "", mot)
    add_dico(m, dico)
    rules = re.sub(r".*/", "", mot)
    while rules != "":
        rule = rules[0:2]
        rules = rules[2:]
        if rule in affixes and affixes[rule]["type"] == "SFX":
            for r in affixes[rule]["rules"]:
                if re.search(r[4] + "$", m):
                    suffixe = re.sub(r"/.*", "", re.sub(r[2] + "$", r[3], m))
                    if suffixe != m:
                        add_dico(suffixe, dico)
                    if "/" in r[3]:
                        load_word(re.sub(r[2] + "$", r[3], m), dico, affixes)
        # Les préfixes (PFX) ne sont jamais chargés par l'original : branche omise.


def load_dico(fichier: Path, dico: dict) -> dict:
    affixes: dict = {}
    aff = fichier.with_suffix(".aff")
    if aff.exists():
        with aff.open(encoding="utf-8") as f:
            for ligne in f:
                af = ligne.split()
                if len(af) == 4 and af[0] == "SFX":
                    affixes[af[1]] = {"type": af[0], "cross_product": af[2], "rules": []}
                if len(af) > 4 and af[0] == "SFX":
                    if af[2] == "0":
                        af[2] = ""
                    if af[3][0] == "0":
                        af[3] = af[3][1:]
                    affixes[af[1]]["rules"].append(af)
    with fichier.with_suffix(".dic").open(encoding="utf-8") as f:
        for mot in f:
            load_word(mot.rstrip("\n"), dico, affixes)
    return dico


def reduce_dico(dico: dict[str, list[str]]) -> dict[str, list[str]]:
    """On élimine les entrées uniques sans accent."""
    return {k: v for k, v in dico.items() if len(v) > 1 or v[0] != unidecode(v[0])}


def charger_bigrammes(formes: dict[str, list[str]], dossier: Path = DOSSIER_DICO) -> dict[str, int]:
    """Fréquences « mot_précédent mot » dont le second mot est dans le dictionnaire."""
    with gzip.open(dossier / "freq5.pz", "rb") as f:
        freq = pickle.load(f)  # fichier vendorisé, de confiance
    retenus = {}
    for k, v in freq.items():
        mots = cle(k).split()
        if len(mots) > 1 and mots[1] in formes:
            retenus[k] = v
    return retenus


# ------------------------------------------------- construction et cache
def construire(dossier: Path = DOSSIER_DICO) -> Dictionnaire:
    complet: dict[str, list[str]] = {}
    load_dico(dossier / "fr-toutesvariantes", complet)
    load_dico(dossier / "complements", complet)

    noms_propres: dict[str, list[str]] = {}
    ambigus: set[str] = set()
    for k, formes in complet.items():
        majuscules = [f for f in formes if f[:1].isupper()]
        if majuscules:
            noms_propres[k] = majuscules
            if any(f[:1].islower() for f in formes):
                ambigus.add(k)

    formes = reduce_dico(complet)
    return Dictionnaire(formes, charger_bigrammes(formes, dossier), noms_propres, ambigus, set(complet))


def _sources(dossier: Path) -> list[Path]:
    return [dossier / n for n in ("fr-toutesvariantes.dic", "fr-toutesvariantes.aff", "complements.dic", "freq5.pz")]


def charger(cache: Path, dossier: Path = DOSSIER_DICO) -> Dictionnaire:
    """Lit le cache s'il est à jour, sinon construit le dictionnaire et l'écrit."""
    if cache.exists() and all(cache.stat().st_mtime >= s.stat().st_mtime for s in _sources(dossier)):
        try:
            with gzip.open(cache, "rb") as f:
                version, d = pickle.load(f)  # cache produit par ce module
            if version == VERSION_CACHE:
                return d
        except (OSError, EOFError, pickle.UnpicklingError, ValueError):
            pass
    d = construire(dossier)
    cache.parent.mkdir(parents=True, exist_ok=True)
    tmp = cache.with_suffix(".tmp")
    with gzip.open(tmp, "wb") as f:
        pickle.dump((VERSION_CACHE, d), f)
    tmp.replace(cache)
    return d
