"""Extraction VLM multi-images (SPEC §5).

- prompt externalisé `config/prompts/extraction_<type>.txt` (VLM-T-005), nom et
  empreinte enregistrés ;
- toutes les images du document sont envoyées (VLM-T-001, ING-T-001) selon
  `VLM_STRATEGIE` : `requete_unique` ou `par_image` (consolidation champ par
  champ, première valeur non nulle, alerte `extraction_divergente:<champ>`) ;
- la sortie brute du modèle est conservée telle quelle (VLM-T-004) ; aucune
  normalisation ici (VLM-T-003).
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Literal

from pydantic import BaseModel, Field

from humatheque.config import reglages
from humatheque.extraction.fournisseurs import FournisseurVLM, InferenceIndisponible
from humatheque.extraction.images import ImageIllisible, ImageInaccessible, compresser, lire
from humatheque.services.base import Appel

__all__ = ["DocumentSansImage", "ExtractionInvalide", "ImageIllisible", "ImageInaccessible", "InferenceIndisponible", "ResultatExtraction",
           "charger_prompt", "extraire"]


class DocumentSansImage(ImageIllisible):
    """Dossier sans image : document invalide (ING-F-008)."""


class ExtractionInvalide(Exception):
    """Réponse du modèle non analysable en JSON (statut `json_invalide`)."""

    def __init__(self, message: str, bruts: list[str], appels: list[Appel]) -> None:
        super().__init__(message)
        self.bruts, self.appels = bruts, appels


class Prompt(BaseModel):
    nom: str
    empreinte: str
    texte: str


class ResultatExtraction(BaseModel):
    fournisseur: str
    modele: str
    strategie: str
    prompt: Prompt
    bruts: list[str] = Field(description="contenus renvoyés par le modèle, tels quels (VLM-T-004)")
    json_extraits: list[dict[str, Any]]
    extraction: dict[str, Any] = Field(description="extraction consolidée, non normalisée")
    alertes: list[str] = Field(default_factory=list)
    appels: list[Appel] = Field(default_factory=list)


def charger_prompt(type_document: str) -> Prompt:
    nom = f"extraction_{type_document}.txt"
    chemin = reglages().dossier_config / "prompts" / nom
    texte = chemin.read_text(encoding="utf-8")
    return Prompt(nom=nom, empreinte=hashlib.sha256(texte.encode("utf-8")).hexdigest()[:12], texte=texte)


def analyser_json(contenu: str) -> dict[str, Any]:
    """JSON du modèle, en tolérant un bloc ```json … ``` ; ValueError sinon."""
    t = contenu.strip()
    m = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", t, re.S)
    if m:
        t = m[1]
    donnees = json.loads(t)
    if not isinstance(donnees, dict):
        raise ValueError("objet JSON attendu")
    return donnees


def _vide(v: Any) -> bool:
    return v is None or v == "" or v == []


def consolider(extraits: list[dict[str, Any]]) -> tuple[dict[str, Any], list[str]]:
    """Champ par champ : première valeur non vide dans l'ordre des images."""
    resultat: dict[str, Any] = {}
    alertes: list[str] = []
    for champ in dict.fromkeys(k for e in extraits for k in e):
        valeurs = [e.get(champ) for e in extraits if not _vide(e.get(champ))]
        resultat[champ] = valeurs[0] if valeurs else None
        if champ != "confidence" and len({json.dumps(v, ensure_ascii=False, sort_keys=True) for v in valeurs}) > 1:
            alertes.append(f"extraction_divergente:{champ}")
    return resultat, alertes


def extraire(fournisseur: FournisseurVLM, images: list[str | bytes], type_document: str,
             strategie: Literal["requete_unique", "par_image"] | None = None,
             lecteur_s3=None) -> ResultatExtraction:
    r = reglages()
    strategie = strategie or r.vlm_strategie
    prompt = charger_prompt(type_document)
    if not images:
        raise DocumentSansImage("dossier sans image")
    donnees = [compresser(lire(i, r.http_timeout, lecteur_s3=lecteur_s3), r.vlm_image_max_size,
                          r.vlm_image_jpeg_quality) for i in images]
    lots = [donnees] if strategie == "requete_unique" else [[d] for d in donnees]

    bruts: list[str] = []
    appels: list[Appel] = []
    extraits: list[dict[str, Any]] = []
    for lot in lots:
        contenu, appel = fournisseur.appeler(prompt.texte, lot)
        bruts.append(contenu)
        appels.append(appel)
        try:
            extraits.append(analyser_json(contenu))
        except ValueError as e:
            raise ExtractionInvalide(f"réponse du modèle non analysable : {e}", bruts, appels) from e

    extraction, alertes = (extraits[0], []) if len(extraits) == 1 else consolider(extraits)
    return ResultatExtraction(fournisseur=fournisseur.nom, modele=fournisseur.modele, strategie=strategie,
                              prompt=prompt, bruts=bruts, json_extraits=extraits, extraction=extraction,
                              alertes=alertes, appels=appels)
