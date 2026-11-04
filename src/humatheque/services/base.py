"""Socle des clients HTTP des services externes (SPEC §6).

Chaque appel est décrit par un `Appel` (requête, réponse, durée, erreur)
pour être journalisé dans le dossier. Toute erreur de transport, réponse
HTTP 4xx/5xx ou réponse non JSON lève `ServiceIndisponible` (statut d'étape
`service_indisponible`, routage incident) ; un dépassement de délai est
distingué (`timeout=True`).
"""

from __future__ import annotations

import time
from typing import Any

import httpx
from pydantic import BaseModel


class Appel(BaseModel):
    service: str
    methode: str
    url: str
    requete: Any = None
    reponse: Any = None
    statut_http: int | None = None
    duree: float = 0.0
    erreur: str | None = None


class ServiceIndisponible(Exception):
    def __init__(self, service: str, message: str, appel: Appel, timeout: bool = False) -> None:
        super().__init__(f"{service} : {message}")
        self.service, self.appel, self.timeout = service, appel, timeout

    @property
    def relancable(self) -> bool:
        """Une erreur de requête (4xx) ne se corrige pas en relançant (essai réel du 04/11)."""
        return not (self.appel.statut_http is not None and 400 <= self.appel.statut_http < 500)


class ClientService:
    """Client d'un service : URL de base, clé facultative (en-tête X-API-Key), délai."""

    def __init__(self, nom: str, base_url: str, cle_api: str = "", timeout: float = 30.0,
                 client: httpx.Client | None = None) -> None:
        if not base_url:
            raise ValueError(f"URL du service {nom} non configurée")
        self.nom, self.base_url = nom, base_url.rstrip("/")
        entetes = {"X-API-Key": cle_api} if cle_api else {}
        self._client = client or httpx.Client(timeout=timeout, headers=entetes)
        if client is not None:
            self._client.headers.update(entetes)
        self.timeout = timeout

    def _executer(self, methode: str, url: str, payload: Any, json_attendu: bool) -> tuple[Any, Appel]:
        appel = Appel(service=self.nom, methode=methode, url=url, requete=payload)
        debut = time.monotonic()
        try:
            r = self._client.request(methode, url, json=payload, timeout=self.timeout)
            appel.statut_http = r.status_code
            r.raise_for_status()
            donnees = r.json() if json_attendu else r.text
            appel.reponse = donnees
            return donnees, appel
        except httpx.TimeoutException as e:
            appel.erreur = f"délai dépassé ({self.timeout} s) : {e}"
            raise ServiceIndisponible(self.nom, appel.erreur, appel, timeout=True) from e
        except httpx.HTTPStatusError as e:
            appel.erreur = f"HTTP {e.response.status_code} : {e.response.text[:500]}"
            raise ServiceIndisponible(self.nom, appel.erreur, appel) from e
        except httpx.HTTPError as e:
            appel.erreur = f"service injoignable : {e}"
            raise ServiceIndisponible(self.nom, appel.erreur, appel) from e
        except ValueError as e:
            appel.erreur = "réponse non JSON"
            raise ServiceIndisponible(self.nom, appel.erreur, appel) from e
        finally:
            appel.duree = round(time.monotonic() - debut, 3)

    def post(self, route: str, payload: dict[str, Any]) -> tuple[Any, Appel]:
        return self._executer("POST", f"{self.base_url}{route}", payload, True)

    def get_texte(self, url: str) -> tuple[str, Appel]:
        return self._executer("GET", url, None, False)


def texte(v: Any) -> str:
    """Chaîne pour un champ texte facultatif (None → "")."""
    if v is None:
        return ""
    if isinstance(v, list):
        return " ; ".join(str(x).strip() for x in v if str(x).strip())
    return str(v).strip()


def liste(v: Any) -> list[str]:
    if v is None or v == "":
        return []
    if isinstance(v, list):
        return [str(x).strip() for x in v if str(x).strip()]
    return [str(v).strip()]
