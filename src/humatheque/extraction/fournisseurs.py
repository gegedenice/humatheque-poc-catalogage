"""Fournisseurs VLM interchangeables (VLM-T-006) : une interface, une classe
OpenAI-compatible paramétrée par fournisseur (repris de `resolve_vlm_provider`
du POC). Changer de modèle ou de fournisseur = changer la configuration."""

from __future__ import annotations

import time
from typing import Any, Protocol

import httpx

from humatheque.config import Reglages
from humatheque.services.base import Appel


class InferenceIndisponible(Exception):
    """Service d'inférence indisponible (statut `inference_indisponible`)."""

    def __init__(self, message: str, appel: Appel, timeout: bool = False) -> None:
        super().__init__(message)
        self.appel, self.timeout = appel, timeout


class FournisseurVLM(Protocol):
    nom: str
    modele: str

    def appeler(self, prompt: str, images: list[str]) -> tuple[str, Appel]:
        """Renvoie le contenu texte de la réponse et l'appel journalisable."""


class FournisseurOpenAICompatible:
    def __init__(self, nom: str, url: str, modele: str, cle: str = "", timeout: float = 180.0,
                 extras: dict[str, Any] | None = None, client: httpx.Client | None = None) -> None:
        if not url:
            raise ValueError(f"URL du fournisseur VLM {nom} non configurée")
        self.nom, self.url, self.modele, self.timeout = nom, url, modele, timeout
        self.extras = extras or {}
        self._client = client or httpx.Client()
        self._entetes = {"Authorization": f"Bearer {cle}"} if cle else {}

    def appeler(self, prompt: str, images: list[str]) -> tuple[str, Appel]:
        payload = {
            "model": self.modele,
            "messages": [{"role": "user", "content": [{"type": "text", "text": prompt}]
                          + [{"type": "image_url", "image_url": {"url": i}} for i in images]}],
            "response_format": {"type": "json_object"},
            **self.extras,
        }
        # Images base64 non journalisées (volumineuses) : on garde leur nombre.
        resume = {**payload, "messages": [{"role": "user", "content": [
            {"type": "text", "text": prompt}, {"type": "images", "nombre": len(images)}]}]}
        appel = Appel(service=f"vlm:{self.nom}", methode="POST", url=self.url, requete=resume)
        debut = time.monotonic()
        try:
            r = self._client.post(self.url, json=payload, headers=self._entetes, timeout=self.timeout)
            appel.statut_http = r.status_code
            r.raise_for_status()
            donnees = r.json()
            appel.reponse = donnees
            return (((donnees.get("choices") or [{}])[0].get("message") or {}).get("content") or ""), appel
        except httpx.TimeoutException as e:
            appel.erreur = f"délai d'inférence dépassé ({self.timeout} s)"
            raise InferenceIndisponible(appel.erreur, appel, timeout=True) from e
        except httpx.HTTPStatusError as e:
            appel.erreur = f"HTTP {e.response.status_code} : {e.response.text[:500]}"
            raise InferenceIndisponible(appel.erreur, appel) from e
        except (httpx.HTTPError, ValueError) as e:
            appel.erreur = f"service d'inférence injoignable ou réponse non JSON : {e}"
            raise InferenceIndisponible(appel.erreur, appel) from e
        finally:
            appel.duree = round(time.monotonic() - debut, 3)


SEPARATEUR_MODELE = "#"  # « hf#Qwen/Qwen2.5-VL-7B-Instruct » : fournisseur et modèle choisi


def table_fournisseurs(r: Reglages) -> dict[str, tuple[str, str, str]]:
    """Fournisseur → (URL, modèle de .env, clé) ; la clé n'est jamais affichée."""
    return {
        "vlm": (r.vlm_api_url, r.vlm_model, ""),
        "openai": (r.openai_api_url, r.openai_model, r.openai_api_key.get_secret_value()),
        "hf": (r.hf_api_url, r.hf_model, r.hf_token.get_secret_value()),
        "albert": (r.albert_api_url, r.albert_model, r.albert_api_key.get_secret_value()),
    }


def fournisseur(nom: str, r: Reglages, client: httpx.Client | None = None) -> FournisseurOpenAICompatible:
    """Fournisseur par nom : vlm (endpoint on-premise), openai, hf, albert ; « nom#modèle »
    remplace le modèle de .env (choix de l'administration)."""
    nom, _, modele_choisi = (nom or r.vlm_fournisseur).partition(SEPARATEUR_MODELE)
    nom = nom.strip().lower()
    table = table_fournisseurs(r)
    if nom not in table:
        raise ValueError(f"fournisseur VLM inconnu : {nom} (admis : {', '.join(table)})")
    url, modele, cle = table[nom]
    return FournisseurOpenAICompatible(nom, url, modele_choisi.strip() or modele, cle, r.vlm_timeout, client=client)


def lister_modeles(nom: str, r: Reglages, timeout: float = 10) -> list[str]:
    """Modèles accessibles chez un fournisseur (`GET …/models` des API OpenAI-compatibles)."""
    url, _, cle = table_fournisseurs(r)[nom]
    if not url:
        raise ValueError("URL non configurée")
    base = url.rsplit("/chat/completions", 1)[0]
    rep = httpx.get(f"{base}/models", headers={"Authorization": f"Bearer {cle}"} if cle else {}, timeout=timeout)
    rep.raise_for_status()
    donnees = rep.json()
    liste = donnees.get("data", donnees) if isinstance(donnees, dict) else donnees
    return sorted({str(m.get("id")) for m in liste if isinstance(m, dict) and m.get("id")})
