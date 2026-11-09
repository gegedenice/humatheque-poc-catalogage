"""Chargement des règles (SPEC §8) : `regles/*.yaml` (PyYAML).

`construire` lit des textes YAML (fichiers ou brouillons de l'interface) :
une erreur de syntaxe ou de structure lève `ReglesInvalides` avec le fichier
et la ligne, sans rien deviner.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from humatheque.config import Reglages, reglages as reglages_par_defaut
from humatheque.regles.modeles import EtapePipeline, Regles, Socle, Variable, Variante, ZoneRegle

FICHIER_VARIABLES = "variables.yaml"
FICHIER_PIPELINE = "pipeline.yaml"
FICHIER_SOCLE = "notice/socle.yaml"
DOSSIER_VARIANTES = "notice/variantes"


class ReglesInvalides(Exception):
    """Fichier de règles illisible : liste de (fichier, ligne, message)."""

    def __init__(self, problemes: list[tuple[str, str, str]]) -> None:
        self.problemes = problemes
        super().__init__("; ".join(f"{f} {l} : {m}".replace("  ", " ") for f, l, m in problemes))




def fichiers_regles(dossier: Path) -> list[str]:
    """Chemins relatifs des fichiers de règles, dans l'ordre de lecture."""
    variantes = sorted(p.relative_to(dossier).as_posix() for p in (dossier / DOSSIER_VARIANTES).glob("*.yaml"))
    return [FICHIER_VARIABLES, FICHIER_PIPELINE, FICHIER_SOCLE, *variantes]


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


def _empreinte(textes: dict[str, str]) -> str:
    h = hashlib.sha256()
    for rel in sorted(textes):
        h.update(rel.encode() + b"\0" + textes[rel].encode("utf-8") + b"\0")
    return h.hexdigest()[:12]


def _zones(rel: str, brutes: list, origine: str, problemes: list) -> list[ZoneRegle]:
    zones = []
    for i, z in enumerate(brutes, start=1):
        try:
            zones.append(ZoneRegle.model_validate({**(z or {}), "origine": origine}))
        except ValidationError as e:
            problemes += _erreurs_pydantic(rel, f"zone {i} ({(z or {}).get('id', '?')})", e)
    return zones


def construire(textes: dict[str, str], *, emplacement: str) -> Regles:
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

    # --- pipeline.yaml
    brut = _yaml(FICHIER_PIPELINE, textes.get(FICHIER_PIPELINE, ""))
    communes: dict[str, list[str]] = {}
    etapes: dict[str, EtapePipeline] = {}
    if _attendu(FICHIER_PIPELINE, isinstance(brut, dict) and isinstance(brut.get("etapes"), dict),
                "clés communes et etapes attendues", problemes):
        communes = {str(k): [str(x) for x in (v or [])] for k, v in (brut.get("communes") or {}).items()}
        for nom, e in brut["etapes"].items():
            try:
                etapes[str(nom)] = EtapePipeline.model_validate({**(e or {}), "id": str(nom)})
            except ValidationError as err:
                problemes += _erreurs_pydantic(FICHIER_PIPELINE, f"étape {nom}", err)

    # --- notice/socle.yaml
    brut = _yaml(FICHIER_SOCLE, textes.get(FICHIER_SOCLE, ""))
    socle = Socle(zones=[], fichier=FICHIER_SOCLE)
    if _attendu(FICHIER_SOCLE, isinstance(brut, dict) and isinstance(brut.get("zones"), list),
                "clés rendus et zones attendues", problemes):
        socle = Socle(rendus={str(k): str(v) for k, v in (brut.get("rendus") or {}).items()},
                      zones=_zones(FICHIER_SOCLE, brut["zones"], "socle", problemes), fichier=FICHIER_SOCLE)

    # --- notice/variantes/*.yaml
    variantes: dict[str, Variante] = {}
    for rel in sorted(k for k in textes if k.startswith(DOSSIER_VARIANTES + "/")):
        v = _yaml(rel, textes[rel])
        if not _attendu(rel, isinstance(v, dict) and v.get("variante"), "clé variante attendue", problemes):
            continue
        nom = str(v["variante"])
        try:
            variante = Variante.model_validate({
                **{k: x for k, x in v.items() if k not in ("ajouter", "remplacer")}, "fichier": rel,
                "ajouter": _zones(rel, v.get("ajouter") or [], nom, problemes),
                "remplacer": _zones(rel, v.get("remplacer") or [], nom, problemes)})
        except ValidationError as e:
            problemes += _erreurs_pydantic(rel, "", e)
            continue
        if nom in variantes:
            problemes.append((rel, "", f"variante « {nom} » déjà définie dans {variantes[nom].fichier}"))
        variantes[nom] = variante

    if problemes:
        raise ReglesInvalides(problemes)
    return Regles(variables=variables, communes=communes, etapes=etapes, socle=socle, variantes=variantes,
                  emplacement=emplacement, horodatage=datetime.now(UTC), empreinte=_empreinte(textes),
                  textes=dict(textes))


def charger_regles(reglages: Reglages | None = None, *, dossier: Path | None = None,
                   brouillons: dict[str, str] | None = None) -> Regles:
    """Charge `REGLES_DIR` (ou `dossier`) ; `brouillons` remplace le texte de certains fichiers."""
    r = reglages or reglages_par_defaut()
    dossier = dossier or r.dossier_regles
    textes = {**lire_textes(dossier), **(brouillons or {})}
    return construire(textes, emplacement=str(dossier))
