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


class FournisseurEndpointHF(FournisseurOpenAICompatible):
    """Endpoint HF dédié en scale-to-zero (`custom-hf-endpoint`) : avant l'inférence,
    vérifie son état, le réveille et attend qu'il soit prêt ; un 503 (endpoint qui
    s'est endormi entre-temps) déclenche la même attente puis un second essai.
    Les appels à l'API de gestion sont rendus par `appels_prealables()` (journal)."""

    def __init__(self, *args: Any, reglages: Reglages, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._reglages = reglages
        self._gestion: list[Appel] = []
        self._pret = False

    def appels_prealables(self) -> list[Appel]:
        appels, self._gestion = self._gestion, []
        return appels

    def _assurer_pret(self) -> None:
        from humatheque.extraction import endpoint_hf

        e = endpoint_hf.attendre_pret(self._reglages, journal=self._gestion)
        if not e.pret and e.etat != "non_configure":
            appel = Appel(service=f"vlm:{self.nom}", methode="POST", url=self.url,
                          erreur=f"endpoint HF non prêt : {e.etat} ({e.libelle})")
            raise InferenceIndisponible(f"endpoint HF dédié non prêt après {self._reglages.hf_endpoint_attente:.0f} s : "
                                        f"{e.etat} ({e.libelle})", appel)
        self._pret = True

    def appeler(self, prompt: str, images: list[str]) -> tuple[str, Appel]:
        if not self._pret:
            self._assurer_pret()
        try:
            return super().appeler(prompt, images)
        except InferenceIndisponible as e:
            if e.appel.statut_http != 503:
                raise
            self._gestion.append(e.appel)  # 503 : endpoint endormi entre-temps
            self._pret = False
            self._assurer_pret()
            return super().appeler(prompt, images)


SEPARATEUR_MODELE = "#"  # « hf#Qwen/Qwen3.5-9B » : fournisseur et modèle choisi
# Anciens noms (lots et réglages enregistrés) → nom actuel (décision du 12/11)
ALIAS = {"vlm": "vlm-local"}


def nom_fournisseur(nom: str | None) -> str:
    nom = (nom or "").strip().lower()
    return ALIAS.get(nom, nom)


def table_fournisseurs(r: Reglages) -> dict[str, tuple[str, str, str]]:
    """Fournisseur → (URL, modèle de .env, clé) ; la clé n'est jamais affichée.
    Modèles ouverts uniquement ; l'ordre est celui des listes de l'interface."""
    return {
        "albert": (r.albert_api_url, r.albert_model, r.albert_api_key.get_secret_value()),
        "vlm-local": (r.vlm_api_url, r.vlm_model, ""),
        "hf": (r.hf_api_url, r.hf_model, r.hf_token.get_secret_value()),
        "custom-hf-endpoint": (r.hf_endpoint_api_url, r.hf_endpoint_api_model, r.hf_token.get_secret_value()),
    }


def modeles_proposes(nom: str) -> list[str] | None:
    """Modèles proposés par config/fournisseurs.yaml (résultats de l'évaluation des VLM) ;
    None : la liste vient de l'API du fournisseur (`/models`)."""
    from humatheque.config import charger_yaml

    f = (charger_yaml("fournisseurs").get("fournisseurs") or {}).get(nom_fournisseur(nom)) or {}
    modeles = f.get("modeles")
    return [str(m) for m in modeles] if isinstance(modeles, list) else None


def fournisseur(nom: str, r: Reglages, client: httpx.Client | None = None) -> FournisseurOpenAICompatible:
    """Fournisseur par nom : albert (défaut), vlm-local (point d'accès on-premise), hf,
    custom-hf-endpoint ; « nom#modèle » remplace le modèle de .env (choix de l'administration)."""
    nom, _, modele_choisi = (nom or r.vlm_fournisseur).partition(SEPARATEUR_MODELE)
    nom = nom_fournisseur(nom)
    table = table_fournisseurs(r)
    if nom not in table:
        raise ValueError(f"fournisseur VLM inconnu : {nom} (admis : {', '.join(table)})")
    url, modele, cle = table[nom]
    if nom == "custom-hf-endpoint":
        return FournisseurEndpointHF(nom, url, modele_choisi.strip() or modele, cle, r.vlm_timeout, client=client,
                                     reglages=r)
    return FournisseurOpenAICompatible(nom, url, modele_choisi.strip() or modele, cle, r.vlm_timeout, client=client)


def lister_modeles(nom: str, r: Reglages, timeout: float = 10) -> list[str]:
    """Modèles proposés : liste de config/fournisseurs.yaml si elle existe, sinon
    `GET …/models` de l'API OpenAI-compatible du fournisseur."""
    proposes = modeles_proposes(nom)
    if proposes is not None:
        return proposes
    url, _, cle = table_fournisseurs(r)[nom_fournisseur(nom)]
    if not url:
        raise ValueError("URL non configurée")
    base = url.rsplit("/chat/completions", 1)[0]
    rep = httpx.get(f"{base}/models", headers={"Authorization": f"Bearer {cle}"} if cle else {}, timeout=timeout)
    rep.raise_for_status()
    donnees = rep.json()
    liste = donnees.get("data", donnees) if isinstance(donnees, dict) else donnees
    return sorted({str(m.get("id")) for m in liste if isinstance(m, dict) and m.get("id")})
