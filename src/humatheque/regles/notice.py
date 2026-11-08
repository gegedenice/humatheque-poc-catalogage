"""Générateur de notice à partir des zones structurées (socle + variante).

Chaque zone et chaque sous-zone sont des objets configurables dans le studio
(page Règles › Notice UNIMARC) ; ce module ne contient aucune règle de
catalogage, seulement l'interprétation de leurs propriétés :

- valeur : constante, variable (attribut), composition de parties, autorité ;
- variable liste : une sous-zone par valeur, ou valeurs jointes (séparateur,
  dernier séparateur) ;
- valeur absente : omettre la sous-zone, valeur de repli, ou vérification
  humaine (la sous-zone est omise et une vérification est demandée) ;
- autorité : une zone par personne / organisme, rendue selon un mode partagé
  (`RENDUS`, choisi par type dans le socle) ;
- une zone dont toutes les sous-zones à variable sont absentes est omise, sauf
  si chacune a une valeur de repli.
"""

from __future__ import annotations

from typing import Any

from humatheque.generateur.modeles import Notice, RegleNonCouverte, SousZone, Zone
from humatheque.regles.modeles import NoticeModele, Regles, SousZoneRegle, ZoneRegle

# Modes de rendu d'une autorité : (description, [(code, attribut)] si PPN, [(code, attribut)] sinon).
RENDUS: dict[str, tuple[str, list[tuple[str, str]], list[tuple[str, str]]]] = {
    "ppn_sinon_nom_prenom": ("PPN ($3) si aligné, sinon nom ($a) et prénom ($b)",
                             [("3", "ppn")], [("a", "nom"), ("b", "prenom")]),
    "ppn_sinon_forme": ("PPN ($3) si aligné, sinon forme ($a)", [("3", "ppn")], [("a", "forme")]),
    "nom_prenom": ("nom ($a) et prénom ($b), sans PPN", [], [("a", "nom"), ("b", "prenom")]),
    "forme": ("forme ($a), sans PPN", [], [("a", "forme")]),
}
ATTRIBUTS = {"personne": ["forme", "nom", "prenom", "ppn"], "organisme": ["forme", "ppn"]}


class GenerationEchec(Exception):
    """Échec du générateur (statut d'étape `generation_echec`)."""


def _manque(x: Any) -> bool:
    return x is None or x == "" or x == [] or x == {}


def _brute(ctx: dict[str, Any], nom: str, element: tuple[str, Any] | None) -> Any:
    return element[1] if element and element[0] == nom else ctx.get(nom)


def _attribut(obj: Any, attribut: str | None) -> Any:
    if isinstance(obj, dict):
        return obj.get(attribut or "forme")
    return obj if attribut in (None, "forme") else None


def _valeurs(ctx: dict[str, Any], nom: str, attribut: str | None, element: tuple[str, Any] | None) -> list[str]:
    """Valeurs (non vides) d'une variable, liste ou non."""
    obj = _brute(ctx, nom, element)
    objets = obj if isinstance(obj, list) else [obj]
    return [str(v) for v in (_attribut(o, attribut) for o in objets) if not _manque(v)]


def _joindre(vals: list[str], sz: SousZoneRegle) -> str:
    dernier = sz.dernier if sz.dernier is not None else sz.separateur
    return vals[0] if len(vals) == 1 else sz.separateur.join(vals[:-1]) + dernier + vals[-1]


def mode_rendu(sz: SousZoneRegle, regles: Regles, rendus: dict[str, str]) -> str:
    var = regles.variables.get(sz.autorite or "")
    return sz.rendu or rendus.get(var.type if var else "", "") or "forme"


class _Rendu:
    def __init__(self) -> None:
        self.sous_zones: list[tuple[str, str]] = []
        self.avec_var = False
        self.rendue = False          # au moins une sous-zone à variable produite depuis les données
        self.sans_repli = False      # une sous-zone à variable absente sans repli
        self.repli = False
        self.verifications: list[str] = []


def _rendre_sous_zone(r: _Rendu, sz: SousZoneRegle, z: ZoneRegle, ctx: dict[str, Any],
                      element: tuple[str, Any] | None, regles: Regles, rendus: dict[str, str]) -> None:
    genre = sz.genre
    if genre == "constante":
        r.sous_zones.append((sz.code, f"{sz.prefixe}{sz.constante}{sz.suffixe}"))
        return
    if genre == "vide":
        return
    r.avec_var = True
    produites: list[tuple[str, str]] = []
    if genre == "autorite":
        obj = _brute(ctx, sz.autorite or "", element)
        if isinstance(obj, list):  # autorité non itérée (ne devrait pas arriver) : premier élément
            obj = obj[0] if obj else None
        if not _manque(obj):
            _, avec_ppn, sinon = RENDUS.get(mode_rendu(sz, regles, rendus), RENDUS["forme"])
            ppn = _attribut(obj, "ppn")
            if avec_ppn and not _manque(ppn):
                produites = [(c, str(_attribut(obj, a))) for c, a in avec_ppn if not _manque(_attribut(obj, a))]
            else:
                produites = [(c, str(_attribut(obj, a))) for c, a in sinon if not _manque(_attribut(obj, a))]
                if produites and avec_ppn:
                    r.repli = True
    elif genre == "composition":
        morceaux = []
        for p in sz.parties or []:
            if p.variable:
                vals = _valeurs(ctx, p.variable, p.attribut, element)
                if not vals:
                    morceaux = None
                    break
                morceaux.append(_joindre(vals, sz))
            else:
                morceaux.append(p.texte or "")
        if morceaux is not None:
            produites = [(sz.code, f"{sz.prefixe}{''.join(morceaux)}{sz.suffixe}")]
    else:  # variable
        vals = _valeurs(ctx, sz.variable or "", sz.attribut, element)
        if vals:
            if sz.liste == "joindre":
                vals = [_joindre(vals, sz)]
            produites = [(sz.code, f"{sz.prefixe}{v}{sz.suffixe}") for v in vals]
    if produites:
        r.sous_zones += produites
        r.rendue = True
        return
    if sz.si_absente == "repli" and sz.repli is not None:
        r.sous_zones.append((sz.code, sz.repli))
        r.repli = True
        return
    r.sans_repli = True
    if sz.si_absente == "verification":
        nom = ", ".join(sz.variables()) or sz.code
        r.verifications.append(f"zone {z.zone} ${sz.code} ({sz.libelle or nom}) : valeur absente, à vérifier [{z.trace}]")


def rendre_zone(z: ZoneRegle, ctx: dict[str, Any], element: tuple[str, Any] | None, regles: Regles,
                rendus: dict[str, str]) -> tuple[Zone | None, _Rendu]:
    r = _Rendu()
    for sz in z.sous_zones:
        _rendre_sous_zone(r, sz, z, ctx, element, regles, rendus)
    if r.avec_var and not r.rendue and r.sans_repli:
        return None, r
    return Zone(etiquette=z.zone, indicateurs=z.indicateurs,
                sous_zones=[SousZone(code=c, valeur=v, regle=z.trace) for c, v in r.sous_zones]), r


def elements(z: ZoneRegle, ctx: dict[str, Any]) -> list[tuple[str, Any] | None]:
    nom = z.iteree
    if not nom:
        return [None]
    obj = ctx.get(nom)
    objets = obj if isinstance(obj, list) else ([obj] if not _manque(obj) else [])
    return [(nom, o) for o in objets if not _manque(o)]


def productible(regles: Regles, z: ZoneRegle) -> bool:
    """Une zone active peut-elle être produite ? Non si toutes ses sous-zones à variable
    reposent sur des variables indisponibles et n'ont pas de repli."""
    if not z.actif:
        return False
    a_variable = [sz for sz in z.sous_zones if sz.variables()]
    if not a_variable:
        return True
    return any(all(regles.variables.get(n) is None or regles.variables[n].disponible for n in sz.variables())
               or sz.si_absente == "repli" for sz in a_variable)


def generer(regles: Regles, profil: str, ctx: dict[str, Any]) -> Notice:
    modeles = regles.profils
    if profil not in modeles:
        raise GenerationEchec(f"profil inconnu : {profil}")
    m: NoticeModele = modeles[profil]
    notice = Notice(profil=profil, version_spec=regles.version)
    for z in m.zones:
        if not z.actif:
            notice.non_couvertes.append(RegleNonCouverte(regle=z.trace, zone=z.zone, statut=z.statut))
            continue
        produit = False
        for el in elements(z, ctx):
            zone, r = rendre_zone(z, ctx, el, regles, m.rendus)
            notice.verifications += r.verifications
            if zone is None:
                continue
            if r.repli:
                notice.replis.append(z.trace)
            zone.ajouter_regle(z.trace, z.statut)
            zone.libelle = z.iteree
            notice.zones.append(zone)
            produit = True
        if not produit and z.priorite == 1:
            notice.alertes.append(f"zone {z.zone} obligatoire non produite ({z.trace})")
    notice.trace.append(f"profil {profil} : {len(notice.zones)} zone(s)")
    return notice
