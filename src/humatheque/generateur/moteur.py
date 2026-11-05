"""Moteur des règles : contexte + spec + profil → notice (SPEC §8).

Le générateur ne lit que le `Contexte` (dict dont les clés sont les `id` de
`Sources`). Il ne contient aucune règle UNIMARC : zones, indicateurs, codes
et valeurs fixes viennent des gabarits de `Regles`.

Rendu :
- règles du profil, statut hors `REGLES_STATUTS_EXCLUS`, dans l'ordre des lignes ;
- `Non généré` : ignorée, listée comme non couverte ;
- `ZZZ II$x…` ouvre une occurrence ; `$x…` rejoint l'occurrence ouverte de la
  zone (même profil) ou en ouvre une avec les indicateurs déclarés par une
  règle de la zone (`##` à défaut) ;
- une source personne / organisme ou de cardinalité n : une occurrence par
  élément (sauf jonction configurée), la colonne `condition` servant
  d'étiquette d'occurrence ; variables d'élément `{ppn}`, `{nom}`,
  `{prenom}`, `{forme}` et `{<source>}` = forme de l'élément ;
- variable absente : repli, sinon fragment omis ; règle de priorité 1 omise →
  alerte `regle_prioritaire_omise:<id>`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from humatheque.config import charger_yaml, reglages
from humatheque.generateur.modeles import Notice, RegleNonCouverte, SousZone, Zone
from humatheque.spec.gabarit import Gabarit, GabaritInvalide, analyser
from humatheque.spec.modeles import Regle, Spec
from humatheque.spec.verificateur import Vocabulaires

RE_VARIABLE = re.compile(r"\{(\w+)\}")


class GenerationEchec(Exception):
    """Échec du générateur (statut d'étape `generation_echec`)."""


@lru_cache
def _cfg() -> dict[str, Any]:
    return charger_yaml("generateur")


def _absente(v: Any) -> bool:
    return v is None or v == "" or v == [] or v == {}


def _remplir(g: Gabarit, ctx: dict[str, Any]) -> list[tuple[str, str]] | None:
    """Sous-zones remplies ; None si une variable manque."""
    for v in g.variables:
        if _absente(ctx.get(v)):
            return None
    return [(code, RE_VARIABLE.sub(lambda m: str(ctx[m[1]]), valeur)) for code, valeur in g.sous_zones]


def _forme(element: Any) -> Any:
    return element.get("forme") if isinstance(element, dict) else element


@dataclass
class _Passe:
    """Une application de règle : contexte (éventuellement d'élément) et libellé."""

    ctx: dict[str, Any]
    detail: str | None = None


class Generateur:
    def __init__(self, spec: Spec, vocab: Vocabulaires | None = None, statuts_exclus: list[str] | None = None) -> None:
        self.spec = spec
        self.vocab = vocab or Vocabulaires.depuis_config()
        self.exclus = set(statuts_exclus if statuts_exclus is not None else reglages().regles_statuts_exclus)
        self.cardinalites = {s.id: s.cardinalite for s in spec.sources}
        self.profils = {p.code for p in spec.profils}
        self.jonctions = {(j["regle"], j["source"]): j["separateur"] for j in _cfg().get("jonctions") or []}
        self.ind_defaut = _cfg().get("indicateurs_par_defaut", "##")
        self._indicateurs = self._indicateurs_declares()

    # ---------------------------------------------------------------- préparation
    def _gabarit(self, r: Regle, texte: str) -> Gabarit:
        try:
            return analyser(texte)
        except GabaritInvalide as e:
            raise GenerationEchec(f"{r.id} : gabarit « {texte} » non analysable ({e})") from e

    def _etiquette(self, r: Regle) -> str:
        if not re.fullmatch(r"\d{3}(\$[0-9A-Za-z])?", r.zone):
            raise GenerationEchec(f"{r.id} : zone « {r.zone} » inutilisable pour un fragment")
        return r.zone[:3]

    def _indicateurs_declares(self) -> dict[str, str]:
        """Étiquette → indicateurs de la première règle (tous profils) qui en déclare."""
        decl: dict[str, str] = {}
        for r in self.spec.regles:
            if r.statut in self.exclus or r.mode_production == self.vocab.mode_non_genere:
                continue
            for texte in (r.gabarit, r.repli):
                if texte:
                    try:
                        g = analyser(texte)
                    except GabaritInvalide:
                        continue
                    if g.etiquette and g.indicateurs:
                        decl.setdefault(g.etiquette, g.indicateurs)
        return decl

    def _source_elements(self, r: Regle) -> str | None:
        for s in r.liste_sources(self.vocab.sep_liste):
            if s in self.vocab.sources_element or self.cardinalites.get(s) == "n":
                return s
        return None

    def _passes(self, r: Regle, contexte: dict[str, Any]) -> list[_Passe]:
        source = self._source_elements(r)
        if source is None:
            return [_Passe(dict(contexte))]
        valeur = contexte.get(source)
        elements = valeur if isinstance(valeur, list) else ([] if _absente(valeur) else [valeur])
        elements = [e for e in elements if not _absente(e)]
        sep = self.jonctions.get((r.id, source))
        if sep is not None:
            formes = [str(_forme(e)) for e in elements if not _absente(_forme(e))]
            return [_Passe({**contexte, source: sep.join(formes) if formes else None}, f"{source} joints")]
        passes = []
        for n, e in enumerate(elements, start=1):
            ctx = dict(contexte)
            if isinstance(e, dict):
                for var in self.vocab.variables_element:
                    ctx[var] = e.get(var)
            ctx[source] = _forme(e)
            passes.append(_Passe(ctx, f"{source}[{n}]"))
        return passes

    # ---------------------------------------------------------------- rendu
    def generer(self, contexte: dict[str, Any], profil: str) -> Notice:
        if profil not in self.profils:
            raise GenerationEchec(f"profil « {profil} » inconnu")
        notice = Notice(profil=profil, version_spec=self.spec.version)
        ouvertes: dict[str, Zone] = {}

        for r in self.spec.regles:
            if profil not in r.liste_profils(self.vocab.sep_liste) or r.statut in self.exclus:
                continue
            if r.mode_production == self.vocab.mode_non_genere:
                notice.non_couvertes.append(RegleNonCouverte(regle=r.id, zone=r.zone, statut=r.statut))
                continue
            principal = self._gabarit(r, r.gabarit)
            repli = self._gabarit(r, r.repli) if r.repli else None
            produit = False
            for passe in self._passes(r, contexte):
                for g, est_repli in ((principal, False), (repli, True)):
                    if g is None:
                        continue
                    sous_zones = _remplir(g, passe.ctx)
                    if sous_zones is None:
                        continue
                    libelle = (r.condition or None) if self._source_elements(r) else None
                    self._ajouter(notice, ouvertes, r, g, sous_zones, est_repli, passe.detail, libelle)
                    produit = True
                    break
                else:
                    notice.trace.append(f"{r.id} ({r.zone}) : donnée absente, fragment omis"
                                        + (f" [{passe.detail}]" if passe.detail else ""))
            if not produit and r.priorite == "1":
                notice.alertes.append(f"regle_prioritaire_omise:{r.id}")
        return notice

    def _ajouter(self, notice: Notice, ouvertes: dict[str, Zone], r: Regle, g: Gabarit,
                 sous_zones: list[tuple[str, str]], est_repli: bool, detail: str | None,
                 libelle: str | None) -> None:
        if g.ouvre_zone:
            etiquette = g.etiquette
            zone = Zone(etiquette=etiquette, indicateurs=g.indicateurs, libelle=libelle)
            notice.zones.append(zone)
            ouvertes[etiquette] = zone
        else:
            etiquette = self._etiquette(r)
            zone = ouvertes.get(etiquette)
            if zone is None:
                zone = Zone(etiquette=etiquette, indicateurs=self._indicateurs.get(etiquette, self.ind_defaut),
                            libelle=libelle)
                notice.zones.append(zone)
                ouvertes[etiquette] = zone
        for code, valeur in sous_zones:
            zone.sous_zones.append(SousZone(code=code, valeur=valeur, regle=r.id))
        zone.ajouter_regle(r.id, r.statut)
        notice.trace.append(f"{r.id} → {etiquette}" + (" (repli)" if est_repli else "")
                            + (f" [{detail}]" if detail else ""))
        if est_repli:
            notice.replis.append(r.id)


def generer(spec: Spec, contexte: dict[str, Any], profil: str) -> Notice:
    return Generateur(spec).generer(contexte, profil)
