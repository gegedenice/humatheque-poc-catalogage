"""Endpoint Hugging Face dédié (fournisseur `custom-hf-endpoint`, décision du 12/11),
repris du POC (`providers.py` : check / wake de l'endpoint LIFT).

L'endpoint est configuré en scale-to-zero (arrêt après 30 min d'inactivité) : un
appel sur un endpoint endormi renvoie 503 et déclenche son redémarrage (2 à 5 min).
L'API de gestion (`HF_ENDPOINTS_API_BASE/{namespace}/{nom}`) donne son état
(`status.state`) et permet de le relancer (`POST …/resume` s'il est en pause).

Avant une extraction, le fournisseur vérifie l'état, réveille l'endpoint si besoin
et attend qu'il soit prêt (`HF_ENDPOINT_ATTENTE` secondes au plus) : un lot lancé
la nuit n'échoue pas parce que l'endpoint dormait. Chaque appel à l'API de gestion
est journalisé dans le dossier (Appel)."""

from __future__ import annotations

import time
from typing import Any, Callable

import httpx
from pydantic import BaseModel

from humatheque.config import Reglages
from humatheque.services.base import Appel

PRET = "running"

# État de l'API de gestion → (ton, libellé)
ETATS: dict[str, tuple[str, str]] = {
    "running": ("ok", "en ligne, prêt pour l'inférence"),
    "scaledToZero": ("veille", "en veille (arrêt après 30 min d'inactivité) : le réveil prend 2 à 5 min"),
    "paused": ("veille", "en pause (arrêt manuel) : « Réveiller » le relance"),
    "initializing": ("demarrage", "en cours de démarrage (2 à 5 min)"),
    "pending": ("demarrage", "en attente de ressources, démarrage imminent"),
    "updating": ("demarrage", "en cours de mise à jour"),
    "updateFailed": ("erreur", "échec de mise à jour : voir sa configuration sur Hugging Face"),
    "failed": ("erreur", "en échec : voir ses journaux sur Hugging Face"),
}


class EtatEndpoint(BaseModel):
    etat: str
    ton: str = "inconnu"
    libelle: str = ""
    replicas: str | None = None
    url: str | None = None
    route_sante: str = "/health"
    erreur: str | None = None

    @property
    def pret(self) -> bool:
        return self.etat == PRET


def configure(r: Reglages) -> bool:
    return bool(r.hf_endpoint_name and r.hf_endpoint_namespace and r.hf_endpoints_api_base)


def _url_gestion(r: Reglages) -> str:
    return f"{r.hf_endpoints_api_base.rstrip('/')}/{r.hf_endpoint_namespace}/{r.hf_endpoint_name}"


def _entetes(r: Reglages) -> dict[str, str]:
    jeton = r.hf_token.get_secret_value()
    return {"Authorization": f"Bearer {jeton}"} if jeton else {}


def _appel(methode: str, url: str, client: httpx.Client, entetes: dict, journal: list[Appel] | None,
           timeout: float = 15) -> httpx.Response | None:
    appel = Appel(service="vlm:custom-hf-endpoint:gestion", methode=methode, url=url)
    debut = time.monotonic()
    try:
        rep = client.request(methode, url, headers=entetes, timeout=timeout)
        appel.statut_http = rep.status_code
        try:
            appel.reponse = rep.json()
        except ValueError:
            appel.reponse = rep.text[:500]
        return rep
    except httpx.HTTPError as e:
        appel.erreur = str(e)
        return None
    finally:
        appel.duree = round(time.monotonic() - debut, 3)
        if journal is not None:
            journal.append(appel)


def etat(r: Reglages, client: httpx.Client | None = None, journal: list[Appel] | None = None) -> EtatEndpoint:
    """État de l'endpoint d'après l'API de gestion (jamais d'exception)."""
    if not configure(r):
        return EtatEndpoint(etat="non_configure", ton="inconnu",
                            libelle="HF_ENDPOINT_NAME, HF_ENDPOINT_NAMESPACE et HF_ENDPOINTS_API_BASE à renseigner")
    rep = _appel("GET", _url_gestion(r), client or httpx.Client(), _entetes(r), journal)
    if rep is None or rep.status_code != 200:
        detail = "injoignable" if rep is None else f"HTTP {rep.status_code}"
        return EtatEndpoint(etat="inconnu", ton="erreur", libelle=f"API de gestion {detail}", erreur=detail)
    info: dict[str, Any] = rep.json()
    statut = info.get("status") or {}
    nom = str(statut.get("state") or "inconnu")
    ton, libelle = ETATS.get(nom, ("inconnu", f"état « {nom} »"))
    pret, cible = statut.get("readyReplica"), statut.get("targetReplica")
    return EtatEndpoint(etat=nom, ton=ton, libelle=libelle,
                        replicas=f"{pret}/{cible}" if pret is not None and cible is not None else None,
                        url=statut.get("url"), route_sante=info.get("healthRoute") or "/health")


def reveiller(r: Reglages, client: httpx.Client | None = None, journal: list[Appel] | None = None) -> EtatEndpoint:
    """En pause : POST /resume ; en veille ou en transition : une requête sur la route
    de santé suffit à lancer le redémarrage (le 503 renvoyé est attendu)."""
    client = client or httpx.Client()
    e = etat(r, client, journal)
    if e.pret or e.etat in ("non_configure", "inconnu"):
        return e
    if e.etat == "paused":
        _appel("POST", f"{_url_gestion(r)}/resume", client, _entetes(r), journal)
    elif e.url:
        _appel("GET", f"{e.url.rstrip('/')}{e.route_sante}", client, _entetes(r), journal, timeout=10)
    return etat(r, client, journal)


def attendre_pret(r: Reglages, client: httpx.Client | None = None, journal: list[Appel] | None = None,
                  attendre: Callable[[float], None] = time.sleep, pas: float = 15.0) -> EtatEndpoint:
    """Réveille l'endpoint si besoin et attend qu'il soit prêt (HF_ENDPOINT_ATTENTE s au plus).
    Les états intermédiaires ne sont pas journalisés (seulement le premier et le dernier)."""
    client = client or httpx.Client()
    e = etat(r, client, journal)
    if e.pret or e.etat in ("non_configure", "inconnu"):
        return e
    e = reveiller(r, client, journal)
    limite = time.monotonic() + r.hf_endpoint_attente
    while not e.pret and e.ton != "erreur" and time.monotonic() < limite:
        attendre(pas)
        e = etat(r, client, None)
    if journal is not None:
        etat(r, client, journal)  # état final, journalisé
    return e
