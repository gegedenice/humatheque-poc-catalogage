"""Chargement des règles (SPEC §8) : `regles/*.yaml` (PyYAML) et `Statuts_services`
(`spec/Statuts_services.csv`, ou Grist selon `SPEC_SOURCE`, avec repli sur le CSV).

`construire` lit des textes YAML (fichiers ou brouillons de l'interface) :
une erreur de syntaxe ou de structure lève `ReglesInvalides` avec le fichier
et la ligne, sans rien deviner.
"""

from __future__ import annotations

import csv
import hashlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import httpx
import yaml
from pydantic import ValidationError

from humatheque.config import Reglages, reglages as reglages_par_defaut
from humatheque.regles.grist import GristInjoignable, lire_tables_grist
from humatheque.regles.modeles import Ligne, Profil, RegleDecision, Regles, StatutService, Variable

FICHIER_VARIABLES = "variables.yaml"
FICHIER_DECISIONS = "decisions.yaml"
DOSSIER_PROFILS = "profils"
TABLE_STATUTS = "Statuts_services"


class ReglesInvalides(Exception):
    """Fichier de règles illisible : liste de (fichier, ligne, message)."""

    def __init__(self, problemes: list[tuple[str, str, str]]) -> None:
        self.problemes = problemes
        super().__init__("; ".join(f"{f} {l} : {m}".replace("  ", " ") for f, l, m in problemes))




def fichiers_regles(dossier: Path) -> list[str]:
    """Chemins relatifs des fichiers de règles, dans l'ordre de lecture."""
    profils = sorted(p.relative_to(dossier).as_posix() for p in (dossier / DOSSIER_PROFILS).glob("*.yaml"))
    return [FICHIER_VARIABLES, FICHIER_DECISIONS, *profils]


def lire_textes(dossier: Path) -> dict[str, str]:
    if not dossier.is_dir():
        raise ReglesInvalides([(str(dossier), "", "dossier des règles introuvable (REGLES_DIR)")])
    textes = {}
    for rel in fichiers_regles(dossier):
        chemin = dossier / rel
        if not chemin.exists():
            raise ReglesInvalides([(rel, "", "fichier manquant")])
        textes[rel] = chemin.read_text(encoding="utf-8")
    return textes


def _yaml(rel: str, texte: str) -> Any:
    try:
        return yaml.safe_load(texte)
    except yaml.YAMLError as e:
        marque = getattr(e, "problem_mark", None)
        ligne = f"ligne {marque.line + 1}" if marque else ""
        raise ReglesInvalides([(rel, ligne, f"YAML invalide : {getattr(e, 'problem', None) or e}")]) from e


def _attendu(rel: str, condition: bool, message: str, problemes: list) -> bool:
    if not condition:
        problemes.append((rel, "", message))
    return condition


def _erreurs_pydantic(rel: str, lieu: str, e: ValidationError) -> list[tuple[str, str, str]]:
    return [(rel, lieu, f"{'.'.join(str(x) for x in err['loc'])} : {err['msg']}") for err in e.errors()]


def lire_statuts_csv(dossier: Path) -> list[dict[str, Any]]:
    chemin = dossier / f"{TABLE_STATUTS}.csv"
    if not chemin.exists():
        raise ReglesInvalides([(f"{TABLE_STATUTS}.csv", "", f"table introuvable dans {dossier}")])
    with chemin.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _empreinte(textes: dict[str, str], statuts: list[StatutService]) -> str:
    h = hashlib.sha256()
    for rel in sorted(textes):
        h.update(rel.encode() + b"\0" + textes[rel].encode("utf-8") + b"\0")
    for s in statuts:
        h.update(repr(s.model_dump()).encode("utf-8"))
    return h.hexdigest()[:12]


def construire(textes: dict[str, str], statuts_bruts: list[dict[str, Any]], *, emplacement: str,
               source: Literal["csv", "grist"] = "csv", emplacement_statuts: str = "",
               avertissements: list[str] | None = None) -> Regles:
    problemes: list[tuple[str, str, str]] = []

    # --- variables.yaml
    brut = _yaml(FICHIER_VARIABLES, textes.get(FICHIER_VARIABLES, ""))
    variables: dict[str, Variable] = {}
    if _attendu(FICHIER_VARIABLES, isinstance(brut, dict), "dictionnaire des variables attendu", problemes):
        for nom, v in brut.items():
            try:
                variables[str(nom)] = Variable.model_validate({**(v or {}), "nom": str(nom)})
            except ValidationError as e:
                problemes += _erreurs_pydantic(FICHIER_VARIABLES, str(nom), e)

    # --- decisions.yaml
    brut = _yaml(FICHIER_DECISIONS, textes.get(FICHIER_DECISIONS, ""))
    faits: dict[str, list[str]] = {}
    decisions: list[RegleDecision] = []
    sinon: dict[str, Any] = {}
    if _attendu(FICHIER_DECISIONS, isinstance(brut, dict), "clés faits, regles et sinon attendues", problemes):
        faits = {str(k): [str(x) for x in (v or [])] for k, v in (brut.get("faits") or {}).items()}
        _attendu(FICHIER_DECISIONS, isinstance(brut.get("regles"), list), "liste « regles » attendue", problemes)
        for i, r in enumerate(brut.get("regles") or [], start=1):
            try:
                decisions.append(RegleDecision.model_validate(r))
            except ValidationError as e:
                problemes += _erreurs_pydantic(FICHIER_DECISIONS, f"règle {i}", e)
        sinon = brut.get("sinon") or {}
        _attendu(FICHIER_DECISIONS, bool(sinon), "« sinon » obligatoire", problemes)

    # --- profils/*.yaml
    profils: dict[str, Profil] = {}
    for rel in sorted(k for k in textes if k.startswith(DOSSIER_PROFILS + "/")):
        p = _yaml(rel, textes[rel])
        if not _attendu(rel, isinstance(p, dict) and isinstance(p.get("lignes"), list),
                        "clés profil et lignes attendues", problemes):
            continue
        lignes = []
        for n, l in enumerate(p["lignes"], start=1):
            donnees = l if isinstance(l, dict) else {"ligne": l}
            try:
                lignes.append(Ligne.model_validate({**donnees, "numero": n}))
            except ValidationError as e:
                problemes += _erreurs_pydantic(rel, f"ligne {n}", e)
        try:
            profil = Profil.model_validate({**p, "lignes": lignes, "fichier": rel})
        except ValidationError as e:
            problemes += _erreurs_pydantic(rel, "", e)
            continue
        if profil.profil in profils:
            problemes.append((rel, "", f"profil « {profil.profil} » déjà défini dans {profils[profil.profil].fichier}"))
        profils[profil.profil] = profil

    statuts = []
    for i, s in enumerate(statuts_bruts, start=2):
        if not any(str(v or "").strip() for v in s.values()):
            continue
        try:
            statuts.append(StatutService.model_validate(s))
        except ValidationError as e:
            problemes += _erreurs_pydantic(f"{TABLE_STATUTS}", f"ligne {i}", e)
    if problemes:
        raise ReglesInvalides(problemes)
    return Regles(variables=variables, profils=profils, faits=faits, decisions=decisions, sinon=sinon,
                  statuts_services=statuts, source=source, emplacement=emplacement,
                  emplacement_statuts=emplacement_statuts, horodatage=datetime.now(UTC),
                  empreinte=_empreinte(textes, statuts), avertissements=avertissements or [], textes=dict(textes))


def lire_statuts(r: Reglages, source: Literal["csv", "grist"] | None = None,
                 client: httpx.Client | None = None) -> tuple[list[dict[str, Any]], str, str, list[str]]:
    """(lignes, source effective, emplacement, avertissements) de Statuts_services."""
    source = source or r.spec_source
    if source == "grist":
        try:
            tables = lire_tables_grist(r.grist_url, r.grist_doc_id, r.grist_api_key.get_secret_value(),
                                       [TABLE_STATUTS], timeout=r.http_timeout, client=client)
            return tables[TABLE_STATUTS], "grist", f"{r.grist_url}/doc/{r.grist_doc_id}", []
        except GristInjoignable as e:
            alerte = f"Grist injoignable ({e}) : repli sur {r.dossier_spec / (TABLE_STATUTS + '.csv')}"
            return lire_statuts_csv(r.dossier_spec), "csv", str(r.dossier_spec), [alerte]
    return lire_statuts_csv(r.dossier_spec), "csv", str(r.dossier_spec), []


def charger_regles(reglages: Reglages | None = None, *, dossier: Path | None = None,
                   source: Literal["csv", "grist"] | None = None, client: httpx.Client | None = None,
                   brouillons: dict[str, str] | None = None) -> Regles:
    """Charge `REGLES_DIR` (ou `dossier`) ; `brouillons` remplace le texte de certains fichiers."""
    r = reglages or reglages_par_defaut()
    dossier = dossier or r.dossier_regles
    textes = {**lire_textes(dossier), **(brouillons or {})}
    statuts, src, empl, avert = lire_statuts(r, source, client)
    return construire(textes, statuts, emplacement=str(dossier), source=src, emplacement_statuts=empl,
                      avertissements=avert)
