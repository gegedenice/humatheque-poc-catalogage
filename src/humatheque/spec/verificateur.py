"""Vérificateur de la spécification (`humatheque check-spec`, SPEC §8).

Signale, sans rien corriger :
- les gabarits et replis non analysables, et leur incohérence avec la zone ;
- les variables absentes de `Sources` ;
- les sources de cardinalité inconnue ;
- les profils inconnus (Regles, Situations) ;
- les valeurs non admises dans `Situations` (etat_sudoc, etat_alma, provenance,
  routage) ;
- les lignes alma-checker et sudoc-checker de `Statuts_services` autres que
  `error` (avertissement : elles sont ignorées) ;
- les sources calculées utilisées par aucune règle (avertissement).

Les vocabulaires viennent de `config/*.yaml`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from humatheque.config import charger_yaml, reglages
from humatheque.spec.gabarit import GabaritInvalide, analyser
from humatheque.spec.modeles import Regle, Spec, decouper

Gravite = Literal["erreur", "avertissement"]


class Constat(BaseModel):
    gravite: Gravite
    code: str
    table: str
    ligne: str
    message: str


class Rapport(BaseModel):
    source: str
    emplacement: str
    version: str
    constats: list[Constat]

    @property
    def erreurs(self) -> list[Constat]:
        return [c for c in self.constats if c.gravite == "erreur"]

    @property
    def avertissements(self) -> list[Constat]:
        return [c for c in self.constats if c.gravite == "avertissement"]

    @property
    def bloquant(self) -> bool:
        return bool(self.erreurs)


@dataclass(frozen=True)
class Vocabulaires:
    cardinalites: set[str]
    sources_element: set[str]
    variables_element: set[str]
    mode_non_genere: str
    type_source_calcul: str
    sep_liste: str
    sep_alternatives: str
    sep_plage: str
    routages: set[str]
    services_par_etats: dict[str, set[str]]
    etats_sudoc: set[str]
    alias_sudoc: dict[str, str]
    etats_alma: set[str]
    provenances: set[str]

    @classmethod
    def depuis_config(cls, dossier: Path | None = None) -> Vocabulaires:
        spec = charger_yaml("spec", dossier)
        sudoc = charger_yaml("etats_sudoc", dossier)
        alma = charger_yaml("etats_alma", dossier)
        prov = charger_yaml("provenance", dossier)
        etats_sudoc = set(sudoc["ordre"]) | {sudoc["etat_inconnu"], sudoc["etat_ambigu"], sudoc["etat_rien"]}
        return cls(
            cardinalites=set(map(str, spec["cardinalites"])),
            sources_element=set(spec["sources_personnes"]) | set(spec["sources_organismes"]),
            variables_element=set(spec["variables_element"]),
            mode_non_genere=spec["mode_non_genere"],
            type_source_calcul=spec["type_source_calcul"],
            sep_liste=spec["separateur_liste"],
            sep_alternatives=spec["separateur_alternatives"],
            sep_plage=spec["separateur_plage"],
            routages=set(spec["routages"]),
            services_par_etats={k: set(v) for k, v in spec["services_routage_par_etats"].items()},
            etats_sudoc=etats_sudoc,
            alias_sudoc=dict(sudoc.get("alias") or {}),
            etats_alma=set(alma["correspondance"].values()),
            provenances=set(prov["valeurs_situation"]),
        )


JOKER = "*"


def etiquettes_zone(zone: str, sep_plage: str) -> list[str] | None:
    """Étiquettes couvertes par la colonne `zone` ; None si non analysable.

    `200$a` → [200] ; `104` → [104] ; `600->607` → [600, …, 607].
    """
    if re.fullmatch(r"\d{3}(\$[0-9A-Za-z])?", zone):
        return [zone[:3]]
    m = re.fullmatch(rf"(\d{{3}}){re.escape(sep_plage)}(\d{{3}})", zone)
    if m and int(m[1]) < int(m[2]):
        return [f"{n:03d}" for n in range(int(m[1]), int(m[2]) + 1)]
    return None


def verifier(spec: Spec, vocab: Vocabulaires | None = None, statuts_exclus: list[str] | None = None) -> Rapport:
    vocab = vocab or Vocabulaires.depuis_config()
    exclus = set(statuts_exclus if statuts_exclus is not None else reglages().regles_statuts_exclus)
    constats: list[Constat] = []

    def signaler(gravite: Gravite, code: str, table: str, ligne: str, message: str) -> None:
        constats.append(Constat(gravite=gravite, code=code, table=table, ligne=ligne, message=message))

    for alerte in spec.avertissements:
        signaler("avertissement", "chargement", "-", "-", alerte)

    # --- Sources
    ids_sources = {s.id for s in spec.sources}
    for s in spec.sources:
        if s.cardinalite not in vocab.cardinalites:
            signaler("erreur", "cardinalite_inconnue", "Sources", s.id,
                     f"cardinalité « {s.cardinalite} » inconnue (admises : {', '.join(sorted(vocab.cardinalites))})")

    # --- Regles
    profils = {p.code for p in spec.profils}
    utilisees: set[str] = set()
    for r in spec.regles:
        if r.statut in exclus:
            continue
        utilisees.update(r.liste_sources(vocab.sep_liste))
        _verifier_regle(r, vocab, ids_sources, profils, signaler, utilisees)

    for s in spec.sources:
        if s.type_source == vocab.type_source_calcul and s.id not in utilisees:
            signaler("avertissement", "source_calculee_inutilisee", "Sources", s.id,
                     "source calculée utilisée par aucune règle (colonne sources ou gabarit)")

    # --- Situations
    for s in spec.situations:
        _verifier_valeurs(s.id, "etat_sudoc", s.etat_sudoc, vocab.etats_sudoc, vocab, signaler, vocab.alias_sudoc)
        _verifier_valeurs(s.id, "etat_alma", s.etat_alma, vocab.etats_alma, vocab, signaler)
        _verifier_valeurs(s.id, "provenance", s.provenance, vocab.provenances, vocab, signaler)
        if s.routage not in vocab.routages:
            signaler("erreur", "routage_inconnu", "Situations", s.id, f"routage « {s.routage} » inconnu")
        for p in decouper(s.profil_cible, vocab.sep_liste):
            if p not in profils:
                signaler("erreur", "profil_inconnu", "Situations", s.id, f"profil cible « {p} » inconnu")

    # --- Statuts_services
    ignores: dict[str, list[str]] = {}
    for st in spec.statuts_services:
        lus = vocab.services_par_etats.get(st.service)
        if lus is not None and st.statut not in lus:
            ignores.setdefault(st.service, []).append(st.statut)
            continue
        if st.routage not in vocab.routages:
            signaler("erreur", "routage_inconnu", "Statuts_services", st.id, f"routage « {st.routage} » inconnu")
    for service, statuts in ignores.items():
        signaler("avertissement", "statut_ignore", "Statuts_services", service,
                 f"lignes ignorées (routage par les états du §6 et Situations) : {', '.join(statuts)}")

    return Rapport(source=spec.source, emplacement=spec.emplacement, version=spec.version, constats=constats)


def _verifier_regle(r: Regle, vocab: Vocabulaires, ids_sources: set[str], profils: set[str], signaler, utilisees: set[str]) -> None:
    for p in r.liste_profils(vocab.sep_liste):
        if p not in profils:
            signaler("erreur", "profil_inconnu", "Regles", r.id, f"profil « {p} » inconnu")
    if not r.liste_profils(vocab.sep_liste):
        signaler("erreur", "profil_inconnu", "Regles", r.id, "aucun profil")

    sources_regle = r.liste_sources(vocab.sep_liste)
    for s in sources_regle:
        if s not in ids_sources:
            signaler("erreur", "variable_inconnue", "Regles", r.id, f"source « {s} » absente de Sources")

    etiquettes = etiquettes_zone(r.zone, vocab.sep_plage)
    if etiquettes is None:
        signaler("erreur", "zone_invalide", "Regles", r.id,
                 f"zone « {r.zone} » non analysable (attendu : ZZZ, ZZZ$x ou ZZZ{vocab.sep_plage}ZZZ)")

    if not r.gabarit and r.mode_production != vocab.mode_non_genere:
        signaler("erreur", "gabarit_invalide", "Regles", r.id, "gabarit vide pour une règle générée")

    element = bool(set(sources_regle) & vocab.sources_element)
    for colonne in ("gabarit", "repli"):
        texte = getattr(r, colonne)
        if not texte:
            continue
        try:
            g = analyser(texte)
        except GabaritInvalide as e:
            signaler("erreur", "gabarit_invalide", "Regles", r.id, f"{colonne} « {texte} » : {e}")
            continue
        if g.ouvre_zone and etiquettes and g.etiquette not in etiquettes:
            signaler("erreur", "gabarit_invalide", "Regles", r.id,
                     f"{colonne} : zone {g.etiquette} différente de la colonne zone ({r.zone})")
        for v in g.variables:
            utilisees.add(v)
            if v in ids_sources:
                continue
            if element and v in vocab.variables_element:
                continue
            signaler("erreur", "variable_inconnue", "Regles", r.id,
                     f"{colonne} : variable {{{v}}} absente de Sources"
                     + (" (variable d'élément sans source personne ou organisme)" if v in vocab.variables_element else ""))


def _verifier_valeurs(id_: str, colonne: str, cellule: str, admises: set[str], vocab: Vocabulaires, signaler, alias: dict[str, str] | None = None) -> None:
    valeurs = decouper(cellule, vocab.sep_alternatives)
    if not valeurs:
        signaler("erreur", f"{colonne}_non_admis", "Situations", id_, f"{colonne} vide")
    for v in valeurs:
        v = (alias or {}).get(v, v)
        if v != JOKER and v not in admises:
            signaler("erreur", f"{colonne}_non_admis", "Situations", id_,
                     f"{colonne} « {v} » non admis (admis : {', '.join(sorted(admises))}, *)")
