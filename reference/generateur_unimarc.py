"""
Générateur Unimarc piloté par la spécification Grist (table Regles).

Principe : le code ne contient AUCUNE règle de catalogage. Il sait seulement
1) lire les règles (API Grist ou instantané JSON figé),
2) garder celles du profil du document,
3) remplir leurs gabarits avec le « contexte » du document,
4) assembler les fragments en zones, puis sérialiser (texte WinIBW ou MARCXML).

Colonnes « machine » attendues dans Regles (à ajouter dans Grist, remplies par la
personne qui code ; les colonnes « humaines » restent la documentation) :
  champ          "200"                 étiquette de zone
  ind            "1#"                  indicateurs (# = blanc)
  fragment       "$a{titre_classement}" sous-zone(s) avec variables {nom}
  fragment_repli "$a{nom}$b{prenom}$4070"  utilisé si une variable de `fragment` manque
  ordre          10                    ordre des sous-zones dans l'occurrence
  occurrence     "701_rapporteur"      clé qui regroupe les fragments d'une même zone
                                        (vide = une occurrence par champ)
  repeter_sur    "reviewers"           liste du contexte : une occurrence par élément
  profils        [liste de références vers Profils]
  statut         seed | à discuter | validé | rejeté

Règle de rendu : un fragment dont une variable est absente est omis (après essai
du repli) ; une zone sans aucun fragment est omise. Pas besoin de conditions
« si sous-titre présent » : l'absence de la donnée suffit.
"""
from __future__ import annotations

import json
import re
import string
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date

VAR = re.compile(r"\{(\w+)\}")


# ---------------------------------------------------------------- lecture de la spec
def charger_depuis_grist(serveur: str, doc_id: str, cle_api: str) -> list[dict]:
    """Lit Regles + Profils via l'API REST Grist et résout les références.

    Dans l'API, une colonne Référence renvoie l'identifiant de ligne (int) et une
    Liste de références renvoie ["L", id1, id2, ...] : on les retraduit en codes.
    """
    import requests

    def records(table):
        r = requests.get(f"{serveur}/api/docs/{doc_id}/tables/{table}/records",
                         headers={"Authorization": f"Bearer {cle_api}"}, timeout=30)
        r.raise_for_status()
        return [{"_id": rec["id"], **rec["fields"]} for rec in r.json()["records"]]

    profils = {p["_id"]: p["code"] for p in records("Profils")}
    regles = records("Regles")
    for rg in regles:
        v = rg.get("profils")
        if isinstance(v, list) and v and v[0] == "L":
            rg["profils"] = [profils[i] for i in v[1:] if i in profils]
    return regles


def figer_instantane(regles: list[dict], version: str) -> str:
    """Écrit la spec dans un JSON daté : c'est ce fichier que la production lit."""
    chemin = f"spec_unimarc_{version}_{date.today():%Y%m%d}.json"
    with open(chemin, "w", encoding="utf-8") as f:
        json.dump(regles, f, ensure_ascii=False, indent=1)
    return chemin


def charger_instantane(chemin: str) -> list[dict]:
    with open(chemin, encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------- moteur
@dataclass
class Resultat:
    zones: list[tuple[str, str, str]] = field(default_factory=list)  # (champ, ind, sous-zones)
    alertes: list[str] = field(default_factory=list)

    def texte(self) -> str:
        return "\n".join(f"{c} {i}{s}" for c, i, s in sorted(self.zones, key=lambda z: z[0]))


def _remplir(gabarit: str, ctx: dict) -> str | None:
    """Remplit un gabarit ; None si une variable est absente ou vide."""
    if not gabarit:
        return None
    for nom in VAR.findall(gabarit):
        v = ctx.get(nom)
        if v is None or v == "" or v == []:
            return None
    return string.Formatter().vformat(gabarit, (), ctx)


def generer(regles: list[dict], ctx: dict, profil: str,
            statuts=("validé",)) -> Resultat:
    res = Resultat()
    actives = [r for r in regles
               if r.get("statut") in statuts and profil in (r.get("profils") or [])
               and r.get("champ") and r.get("fragment")]

    # occurrences[(champ, ind, cle)] -> liste de (ordre, fragment)
    occurrences: dict[tuple, list] = defaultdict(list)
    for r in actives:
        cle = r.get("occurrence") or r["champ"]
        liste = r.get("repeter_sur")
        elements = ctx.get(liste) or [] if liste else [None]
        for n, el in enumerate(elements):
            c = dict(ctx, **el) if isinstance(el, dict) else ctx
            frag = _remplir(r["fragment"], c) or _remplir(r.get("fragment_repli", ""), c)
            if frag is None:
                if str(r.get("priorite")) == "1":
                    res.alertes.append(f"{r['id']} ({r['champ']}) : donnée manquante, zone prioritaire")
                continue
            occurrences[(r["champ"], r.get("ind") or "##", f"{cle}#{n}")].append((r.get("ordre") or 0, frag))

    for (champ, ind, _), frags in occurrences.items():
        res.zones.append((champ, ind, "".join(f for _, f in sorted(frags, key=lambda x: x[0]))))
    return res


# ---------------------------------------------------------------- sérialisation MARCXML
def vers_marcxml(res: Resultat) -> str:
    from pymarc import Field, Record, Subfield, record_to_xml

    rec = Record(force_utf8=True)
    for champ, ind, sz in res.zones:
        parts = [p for p in sz.split("$") if p]
        if champ < "010":                       # zones de contrôle : pas de sous-zones
            rec.add_field(Field(tag=champ, data="".join(p[1:] for p in parts)))
            continue
        rec.add_field(Field(tag=champ,
                            indicators=[c if c != "#" else " " for c in ind],
                            subfields=[Subfield(p[0], p[1:]) for p in parts]))
    return record_to_xml(rec).decode("utf-8")


# ---------------------------------------------------------------- démonstration
if __name__ == "__main__":
    TO, TR = "these_originale_imprimee", "these_reproduction_imprimee"
    V, TOUS = "validé", [TO, TR]
    regles = [
        dict(id="R005", champ="101", ind="0#", fragment="$a{language}", ordre=10, profils=TOUS, statut=V, priorite=1),
        dict(id="R010", champ="105", ind="##", fragment="$bm$ba", ordre=20, profils=[TO], statut=V),
        dict(id="R011", champ="105", ind="##", fragment="$bv$ba", ordre=20, profils=[TR], statut=V),
        dict(id="R013", champ="105", ind="##", fragment="$c0$d0", ordre=30, profils=TOUS, statut=V),
        dict(id="R022", champ="200", ind="1#", fragment="$a{titre_classement}", ordre=10, profils=TOUS, statut=V, priorite=1),
        dict(id="R024", champ="200", ind="1#", fragment="$e{subtitle}", ordre=30, profils=TOUS, statut=V),
        dict(id="R025", champ="200", ind="1#", fragment="$f{author}", ordre=40, profils=TOUS, statut=V),
        dict(id="R032", champ="328", ind="#0", fragment="$zReproduction de", ordre=5, profils=[TR], statut=V),
        dict(id="R033", champ="328", ind="#0", fragment="$b{degree_type}", ordre=10, profils=TOUS, statut=V),
        dict(id="R035", champ="328", ind="#0", fragment="$e{granting_institution}", ordre=30, profils=TOUS, statut=V),
        dict(id="R036", champ="328", ind="#0", fragment="$d{defense_year}", ordre=40, profils=TOUS, statut=V),
        dict(id="R044", champ="700", ind="#1", fragment="$3{ppn}$4070", fragment_repli="$a{nom}$b{prenom}$4070",
             repeter_sur="auteurs", profils=TOUS, statut=V, priorite=1),
        dict(id="R048", champ="701", ind="#1", fragment="$3{ppn}$4958$4555", fragment_repli="$a{nom}$b{prenom}$4958$4555",
             occurrence="701_rapporteur", repeter_sur="reviewers", profils=TOUS, statut=V),
        dict(id="R099", champ="999", ind="##", fragment="$aen discussion", profils=TOUS, statut="à discuter"),
    ]
    contexte = {
        "language": "fre", "titre_classement": "Les @coraux tempérés", "subtitle": None,
        "author": "Marie Durand", "degree_type": "Thèse de doctorat",
        "granting_institution": "Université de Montpellier", "defense_year": "1998",
        "auteurs": [{"ppn": None, "nom": "Durand", "prenom": "Marie"}],
        "reviewers": [{"ppn": "026594803"}, {"ppn": None, "nom": "Martin", "prenom": "Jean"}],
    }
    for profil in (TO, TR):
        r = generer(regles, contexte, profil)
        print(f"--- {profil}\n{r.texte()}")
        print("alertes :", r.alertes or "aucune")
    try:
        print(vers_marcxml(generer(regles, contexte, TO))[:300], "…")
    except ImportError:
        print("(pymarc non installé : pip install pymarc)")
