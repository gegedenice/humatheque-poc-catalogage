"""Configuration : réglages lus dans l'environnement (`.env`), chemins du
projet et accès aux fichiers `config/*.yaml`.

Aucun secret n'a de valeur par défaut ici : ils viennent de `.env`.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


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
    regles_dir: Path = Path("regles")

    # Extraction VLM
    vlm_api_url: str = ""
    vlm_model: str = "google/gemma-4-26B-A4B-it"
    vlm_strategie: Literal["requete_unique", "par_image"] = "requete_unique"
    vlm_timeout: float = 180.0

    # Services
    alma_checker_api_url: str = ""
    alma_api_key: SecretStr = SecretStr("")
    sudoc_checker_api_url: str = ""
    idref_qualinka_api_url: str = ""
    idref_qualinka_api_key: SecretStr = SecretStr("")
    dewey_classification_api_url: str = ""
    classification_api_key: SecretStr = SecretStr("")
    # idref-linker /align/person peut dépasser une minute (relevé : 65 s)
    idref_timeout: float = 180.0
    # Seuil de la première classe Dewey (au_dessus_seuil / sous_seuil) ;
    # vide = seuil non fixé (Statuts_services : « Seuil à fixer »)
    dewey_seuil: float | None = None

    # VLM : fournisseurs de modèles ouverts, API OpenAI-compatibles (repris du POC) ;
    # Albert par défaut (décision du 11/11), OpenAI retiré
    vlm_fournisseur: str = "albert"
    vlm_image_max_size: int = 768
    vlm_image_jpeg_quality: int = 85
    hf_api_url: str = "https://router.huggingface.co/v1/chat/completions"
    hf_model: str = "google/gemma-4-26B-A4B-it"
    hf_token: SecretStr = SecretStr("")
    albert_api_url: str = "https://albert.api.etalab.gouv.fr/v1/chat/completions"
    albert_model: str = "google/gemma-4-31B-it"
    albert_api_key: SecretStr = SecretStr("")
    # Endpoint HF dédié en scale-to-zero (fournisseur custom-hf-endpoint, décision du 12/11) :
    # inférence OpenAI-compatible (jeton HF_TOKEN) et API de gestion (état, réveil)
    hf_endpoint_api_url: str = ""
    hf_endpoint_api_model: str = ""
    hf_endpoint_name: str = ""
    hf_endpoint_namespace: str = ""
    hf_endpoints_api_base: str = "https://api.endpoints.huggingface.cloud/v2/endpoint"
    hf_endpoint_attente: float = 420.0  # attente maximale du réveil (s)

    # Exécution
    code_etab: str = "EPHE"
    max_workers: int = 2
    http_timeout: float = 30.0
    relances_auto: int = 2
    relance_delai: float = 2.0  # secondes, multiplié par le numéro de tentative
    provenance_confirmation_llm: bool = False

    # Ingestion MinIO
    minio_endpoint: str = ""
    minio_bucket: str = ""
    minio_prefixe: str = "poc/"
    minio_access_key: SecretStr = SecretStr("")
    minio_secret_key: SecretStr = SecretStr("")
    # Déclencheurs automatiques (SPEC §3.1, étape 8) : valeurs initiales, modifiables
    # ensuite dans la page d'administration (enregistrées en base).
    ingestion_auto: bool = False          # balayage périodique du bucket
    balayage_intervalle: int = 60         # minutes
    planification_cron: str = ""          # vide = pas de traitement à heure fixe
    evenements_delai: int = 60            # secondes de calme avant de traiter après un évènement S3

    # Mot de passe de l'application (vide = pas d'authentification)
    mot_de_passe: SecretStr = SecretStr("")


    @field_validator("dewey_seuil", mode="before")
    @classmethod
    def _vide_none(cls, v: Any) -> Any:
        return None if isinstance(v, str) and not v.strip() else v

    @property
    def dossier_regles(self) -> Path:
        """REGLES_DIR (relatif à la racine du dépôt, ou absolu)."""
        return self.regles_dir if self.regles_dir.is_absolute() else self.racine / self.regles_dir

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
