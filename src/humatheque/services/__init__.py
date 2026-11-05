"""Clients HTTP des services externes : alma-checker, sudoc-checker,
idref-linker, dewey-classifier (SPEC §6). Aucun service n'est réimplémenté."""

from humatheque.services.base import Appel, ClientService, ServiceIndisponible

__all__ = ["Appel", "ClientService", "ServiceIndisponible"]
