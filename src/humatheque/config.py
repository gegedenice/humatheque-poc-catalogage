"""Configuration : réglages lus dans l'environnement (`.env`), chemins du
projet et accès aux fichiers `config/*.yaml`.

Aucun secret n'a de valeur par défaut ici : ils viennent de `.env`.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


def _racine_par_defaut() -> Path:
    """Racine du dépôt : `HUMATHEQUE_RACINE`, sinon deux niveaux au-dessus de `src/humatheque`."""
    env = os.getenv("HUMATHEQUE_RACINE")
    if env:
        return Path(env).resolve()
    return Path(__file__).resolve().parents[2]


class Reglages(BaseSettings):
    """Réglages de l'application (SPEC §10)."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Chemins
    racine: Path = _racine_par_defaut()

    # Spécification
    spec_source: Literal["csv", "grist"] = "csv"
    grist_url: str = "http://grist:8484"
    grist_doc_id: str = ""
    grist_api_key: SecretStr = SecretStr("")
    regles_statuts_exclus: Annotated[list[str], NoDecode] = ["rejeté"]

    # Extraction VLM
    vlm_api_url: str = ""
    vlm_model: str = "google/gemma-4-26B-A4B-it"
    vlm_strategie: Literal["requete_unique", "par_image"] = "requete_unique"
    vlm_timeout: float = 180.0

    # Services
    alma_checker_api_url: str = ""
    alma_api_key: SecretStr = SecretStr("")
    sudoc_checker_api_url: str = ""
    sudoc_seuil_correspondance: float | None = None
    idref_qualinka_api_url: str = ""
    idref_qualinka_api_key: SecretStr = SecretStr("")
    dewey_classification_api_url: str = ""
    classification_api_key: SecretStr = SecretStr("")

    # Exécution
    code_etab: str = "EPHE"
    max_workers: int = 2
    http_timeout: float = 30.0
    relances_auto: int = 2
    provenance_confirmation_llm: bool = False

    # Ingestion MinIO
    minio_endpoint: str = ""
    minio_bucket: str = ""
    minio_prefixe: str = "poc/"
    minio_access_key: SecretStr = SecretStr("")
    minio_secret_key: SecretStr = SecretStr("")
    ingestion_auto: bool = False
    planification_cron: str = ""

    @field_validator("regles_statuts_exclus", mode="before")
    @classmethod
    def _liste_separee_par_virgules(cls, v: Any) -> Any:
        if isinstance(v, str):
            return [x.strip() for x in v.split(",") if x.strip()]
        return v

    @property
    def dossier_spec(self) -> Path:
        return self.racine / "spec"

    @property
    def dossier_config(self) -> Path:
        return self.racine / "config"

    @property
    def dossier_data(self) -> Path:
        return self.racine / "data"


@lru_cache
def reglages() -> Reglages:
    return Reglages()


def charger_yaml(nom: str, dossier: Path | None = None) -> dict[str, Any]:
    """Lit `config/<nom>.yaml` ; erreur explicite si le fichier manque."""
    chemin = (dossier or reglages().dossier_config) / f"{nom}.yaml"
    if not chemin.exists():
        raise FileNotFoundError(f"Fichier de configuration introuvable : {chemin}")
    with chemin.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}
