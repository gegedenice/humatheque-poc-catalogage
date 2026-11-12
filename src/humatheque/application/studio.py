"""Studio de catalogage (SPEC §9.3) : édition de la notice structurée (socle et
variantes).

Chaque enregistrement réécrit un seul fichier de `regles/` (socle ou variante)
par `enregistrer_regles` : vérification, archive, journal,
rechargement. Aucune règle ici : seulement la lecture et l'écriture des objets
configurés dans l'interface.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import yaml
from pydantic import BaseModel, Field

from humatheque.application import ActionImpossible, Introuvable
from humatheque.regles.chargement import FICHIER_SOCLE
from humatheque.regles.modeles import NoticeModele, Variante, ZoneRegle
from humatheque.regles.notice import RENDUS, productible

if TYPE_CHECKING:
    from humatheque.application import Application
    from humatheque.application.regles_admin import EnregistrementRegles

SOCLE = "socle"

ENTETE_SOCLE = """\
# Socle de la notice : zones communes à toutes les variantes (studio, onglet
# « Notice UNIMARC »). Fichier écrit par le studio ; chaque zone a un
# identifiant, une étiquette, des indicateurs et des sous-zones configurables :
#   constante | variable (attribut) | parties (composition) | autorite (rendu)
#   prefixe, suffixe ; liste : repeter | joindre (separateur, dernier)
#   si_absente : omettre | repli (valeur) | verification (humaine)
# rendus : mode de rendu d'une autorité selon son type (personne, organisme).
"""
ENTETE_VARIANTE = """\
# Variante de la notice : modifications ciblées du socle (studio, onglet
# « Notice UNIMARC »). ajouter : zones propres à la variante ; remplacer :
# zones du socle modifiées pour la variante (même id) ; retirer : ids du socle.
"""
class ZoneVue(BaseModel):
    zone: ZoneRegle
    origine: str = Field(description="socle, modifiée (variante), ajoutée (variante)")
    productible: bool = True
    retiree: bool = Field(False, description="zone du socle retirée par la variante")


class VueNotice(BaseModel):
    variante: str = Field(description="« socle » ou nom de la variante")
    variantes: list[dict[str, str]]
    zones: list[ZoneVue]
    rendus: dict[str, str]
    modes_rendu: dict[str, str]
    fichier: str


def _propre(z: ZoneRegle) -> dict[str, Any]:
    """Zone → dict YAML sans valeurs par défaut ni origine."""
    d = z.model_dump(exclude_defaults=True, by_alias=True, exclude={"origine"})
    d["id"], d["zone"] = z.id, z.zone
    if z.sous_zones:
        d["sous_zones"] = [{"code": sz.code, **sz.model_dump(exclude_defaults=True, exclude={"code"})}
                           for sz in z.sous_zones]
    ordre = ["id", "zone", "libelle", "indicateurs", "sous_zones", "priorite", "à_valider", "actif", "remarque"]
    return {k: d[k] for k in ordre if k in d}


def _yaml(entete: str, donnees: dict[str, Any]) -> str:
    return entete + "\n" + yaml.safe_dump(donnees, allow_unicode=True, sort_keys=False, width=110)


class Studio:
    # -------------------------------------------------------------- notice : lecture
    def vue_notice_modele(self: "Application", variante: str = SOCLE) -> VueNotice:
        r = self.regles
        variantes = [{"nom": SOCLE, "libelle": "Socle commun"}] + [
            {"nom": n, "libelle": v.libelle or n} for n, v in r.variantes.items()]
        if variante == SOCLE:
            zones = [ZoneVue(zone=z, origine=SOCLE, productible=productible(r, z)) for z in r.socle.zones]
            fichier = r.socle.fichier
        else:
            v = self._variante(variante)
            modele: NoticeModele = r.profils[variante]
            remplacees = {z.id for z in v.remplacer}
            ajoutees = {z.id for z in v.ajouter}
            zones = [ZoneVue(zone=z, productible=productible(r, z),
                             origine="modifiée" if z.id in remplacees else "ajoutée" if z.id in ajoutees else SOCLE)
                     for z in modele.zones]
            zones += [ZoneVue(zone=z, origine=SOCLE, retiree=True, productible=False)
                      for z in r.socle.zones if z.id in v.retirer]
            fichier = v.fichier
        zones.sort(key=lambda zv: zv.zone.zone)  # ordre de la notice (tri stable)
        return VueNotice(variante=variante, variantes=variantes, zones=zones, rendus=r.socle.rendus,
                         modes_rendu={k: d for k, (d, _, _) in RENDUS.items()}, fichier=fichier)

    def _variante(self: "Application", nom: str) -> Variante:
        v = self.regles.variantes.get(nom)
        if v is None:
            raise Introuvable(f"variante {nom}")
        return v

    def zone_regle(self: "Application", variante: str, zone_id: str) -> ZoneRegle:
        zones = self.regles.socle.zones if variante == SOCLE else (
            self.regles.profils[self._variante(variante).variante].zones
            + [z for z in self.regles.socle.zones if z.id in self._variante(variante).retirer])
        z = next((z for z in zones if z.id == zone_id), None)
        if z is None:
            raise Introuvable(f"zone {zone_id}")
        return z

    def apercu_zone(self: "Application", zone: ZoneRegle, variante: str = SOCLE) -> tuple[list[str], list[str]]:
        """Zone(s) produite(s) sur le contexte d'exemple : lignes WinIBW et vérifications demandées."""
        from humatheque.regles.notice import elements, rendre_zone

        variables = self.exemple_variables()
        lignes, alertes = [], []
        try:
            for el in elements(zone, variables):
                produite, r = rendre_zone(zone, variables, el, self.regles, self.regles.socle.rendus)
                alertes += r.verifications
                if produite:
                    lignes.append(produite.texte())
        except Exception as e:  # noqa: BLE001 (brouillon incomplet, affiché sans aperçu)
            alertes.append(f"aperçu impossible : {e}")
        return lignes, alertes

    def variables_studio(self: "Application") -> dict[str, dict[str, Any]]:
        """Variables proposées dans l'éditeur : type, liste, source, description, exemple."""
        from humatheque.application.regles_admin import _apercu_valeur

        exemple = self.exemple_variables()
        return {n: {**v.model_dump(), "exemple": _apercu_valeur(exemple.get(n))} for n, v in self.regles.variables.items()}

    # -------------------------------------------------------------- notice : écriture
    def _ecrire_socle(self: "Application", zones: list[ZoneRegle], auteur: str,
                      rendus: dict[str, str] | None = None) -> "EnregistrementRegles":
        texte = _yaml(ENTETE_SOCLE, {"rendus": rendus if rendus is not None else dict(self.regles.socle.rendus),
                                     "zones": [_propre(z) for z in zones]})
        return self.enregistrer_regles(FICHIER_SOCLE, texte, auteur)

    def _ecrire_variante(self: "Application", v: Variante, auteur: str) -> "EnregistrementRegles":
        d: dict[str, Any] = {"variante": v.variante, "libelle": v.libelle}
        if v.description:
            d["description"] = v.description
        if v.ajouter:
            d["ajouter"] = [_propre(z) for z in v.ajouter]
        if v.remplacer:
            d["remplacer"] = [_propre(z) for z in v.remplacer]
        if v.retirer:
            d["retirer"] = list(v.retirer)
        return self.enregistrer_regles(v.fichier, _yaml(ENTETE_VARIANTE, d), auteur)

    def enregistrer_zone(self: "Application", variante: str, ancien_id: str | None, zone: ZoneRegle,
                         portee: str, auteur: str) -> "EnregistrementRegles":
        """Ajoute ou modifie une zone. `portee` : « socle » (toutes les variantes) ou
        « variante » (remplacement ou ajout propre à la variante affichée)."""
        if not zone.id.strip() or not zone.zone.strip():
            raise ActionImpossible("l'identifiant et l'étiquette de la zone sont requis")
        socle = list(self.regles.socle.zones)
        if portee == SOCLE:
            i = next((k for k, z in enumerate(socle) if z.id == ancien_id), None) if ancien_id else None
            if ancien_id and i is None:
                raise ActionImpossible(f"la zone {ancien_id} n'est pas dans le socle : enregistrez-la pour la variante")
            if i is None:
                socle.append(zone)
            else:
                socle[i] = zone
            return self._ecrire_socle(socle, auteur)
        v = self._variante(variante).model_copy(deep=True)
        ids_socle = {z.id for z in socle}
        v.remplacer = [z for z in v.remplacer if z.id != ancien_id]
        v.ajouter = [z for z in v.ajouter if z.id != ancien_id]
        if zone.id in ids_socle:
            v.remplacer.append(zone)
        else:
            v.ajouter.append(zone)
        return self._ecrire_variante(v, auteur)

    def modifier_presence_zone(self: "Application", variante: str, zone_id: str, operation: str,
                               auteur: str) -> "EnregistrementRegles":
        """Opérations sur une zone : retirer / remettre (variante), revenir au socle
        (annule le remplacement), supprimer (socle ou zone ajoutée par la variante)."""
        if variante == SOCLE:
            if operation != "supprimer":
                raise ActionImpossible(f"opération « {operation} » impossible sur le socle")
            zones = [z for z in self.regles.socle.zones if z.id != zone_id]
            if len(zones) == len(self.regles.socle.zones):
                raise Introuvable(f"zone {zone_id}")
            return self._ecrire_socle(zones, auteur)
        v = self._variante(variante).model_copy(deep=True)
        if operation == "retirer":
            v.retirer = [*v.retirer, zone_id] if zone_id not in v.retirer else v.retirer
            v.remplacer = [z for z in v.remplacer if z.id != zone_id]
        elif operation == "remettre":
            v.retirer = [i for i in v.retirer if i != zone_id]
        elif operation == "revenir":
            v.remplacer = [z for z in v.remplacer if z.id != zone_id]
        elif operation == "supprimer":
            v.ajouter = [z for z in v.ajouter if z.id != zone_id]
        else:
            raise ActionImpossible(f"opération « {operation} » inconnue")
        return self._ecrire_variante(v, auteur)

    def enregistrer_rendus(self: "Application", rendus: dict[str, str], auteur: str) -> "EnregistrementRegles":
        for t, mode in rendus.items():
            if mode not in RENDUS:
                raise ActionImpossible(f"mode de rendu « {mode} » inconnu pour {t}")
        return self._ecrire_socle(list(self.regles.socle.zones), auteur, rendus)
