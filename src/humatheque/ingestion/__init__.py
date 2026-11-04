"""Ingestion des documents (SPEC §3.1) depuis un stockage S3 (MinIO ou tout
service compatible S3) ou un téléversement de dossiers.

- un dossier = un document (ING-F-001, ING-F-002) ; ses images, triées par
  nom de fichier ; identifiant = chemin du dossier (`TH/TH-01`) ;
- le type de document vient du dossier parent `TH` ou `MM` (ING-F-009,
  config/ingestion.yaml) ;
- dossier sans image ou de type inconnu : document invalide (ING-F-008).

Les images du bucket sont désignées par une URI `s3://<bucket>/<clé>`, lue
par le client S3 (aucune URL publique n'est supposée).
"""

from __future__ import annotations

import os
from collections import defaultdict
from functools import lru_cache
from pathlib import PurePosixPath
from typing import Any, Protocol

from pydantic import BaseModel, Field

from humatheque.config import Reglages, charger_yaml


class DepotIndisponible(Exception):
    """Stockage S3 non configuré, injoignable ou accès refusé."""


@lru_cache
def _cfg() -> dict[str, Any]:
    return charger_yaml("ingestion")


class DocumentDetecte(BaseModel):
    id: str = Field(description="chemin du dossier, ex. TH/TH-01")
    type_document: str | None
    images: list[str] = Field(default_factory=list, description="URI ou chemins, triés par nom")
    invalide: str | None = Field(None, description="motif si le document est invalide (ING-F-008)")


def est_image(nom: str) -> bool:
    return PurePosixPath(nom).suffix.lower().lstrip(".") in _cfg()["extensions_images"]


def constituer_documents(chemins: list[str], type_par_defaut: str | None = None,
                         vers_image=lambda c: c) -> list[DocumentDetecte]:
    """Regroupe des chemins de fichiers (relatifs, séparés par /) en documents.

    Un chemin terminé par / désigne un dossier (marqueur S3 de dossier vide).
    L'identifiant part du dossier de type (`TH/TH-01`) quand il existe.
    """
    types = _cfg()["types_dossiers"]
    dossiers: dict[tuple[str, ...], list[str]] = defaultdict(list)
    for chemin in chemins:
        parties = tuple(p for p in chemin.split("/") if p)
        if chemin.endswith("/"):
            dossier, fichier = parties, None
        else:
            dossier, fichier = parties[:-1], parties[-1] if parties else None
        if not dossier:
            continue
        # Un marqueur de dossier de type (TH/) n'est pas un document.
        if fichier is None and dossier[-1] in types:
            continue
        dossiers[dossier]
        if fichier and est_image(fichier):
            dossiers[dossier].append(chemin)

    documents = []
    for dossier, images in sorted(dossiers.items()):
        rang = max((i for i, p in enumerate(dossier[:-1]) if p in types), default=None)
        if rang is not None:
            ident, type_doc = "/".join(dossier[rang:]), types[dossier[rang]]
        else:
            ident, type_doc = "/".join(dossier), type_par_defaut
        invalide = None
        if not images:
            invalide = "dossier sans image"
        elif type_doc is None:
            invalide = f"type de document inconnu (dossier parent attendu : {', '.join(types)})"
        documents.append(DocumentDetecte(id=ident, type_document=type_doc, invalide=invalide,
                                         images=[vers_image(c) for c in sorted(images, key=lambda c: c.rsplit("/", 1)[-1])]))
    # Dossiers ne contenant que des sous-dossiers (TH/, poc/…) : pas des documents.
    parents = {d.id.rsplit("/", 1)[0] for d in documents if "/" in d.id}
    return [d for d in documents if not (d.invalide == "dossier sans image" and d.id in parents)]


# ---------------------------------------------------------------- stockage S3
class Depot(Protocol):
    bucket: str
    prefixe: str

    def lister(self) -> list[str]:
        """Clés des objets sous le préfixe (dossiers vides : clés terminées par /)."""

    def lire(self, cle: str) -> bytes: ...


def uri(bucket: str, cle: str) -> str:
    return f"s3://{bucket}/{cle}"


def cle_depuis_uri(u: str, bucket: str) -> str:
    entete = f"s3://{bucket}/"
    if not u.startswith(entete):
        raise DepotIndisponible(f"URI hors du bucket configuré : {u}")
    return u[len(entete):]


def parametres_connexion(endpoint: str) -> tuple[str, bool]:
    """`https://minio.exemple.org` → (`minio.exemple.org`, True) ; http:// ou localhost → non sécurisé."""
    securise = not (endpoint.startswith("http://") or "localhost" in endpoint or "127.0.0.1" in endpoint)
    hote = endpoint.replace("http://", "").replace("https://", "").rstrip("/")
    return hote, securise


class DepotS3:
    """Bucket S3 (MinIO ou autre service compatible) via la bibliothèque `minio`."""

    def __init__(self, endpoint: str, bucket: str, prefixe: str, cle_acces: str, cle_secrete: str) -> None:
        from minio import Minio

        hote, securise = parametres_connexion(endpoint)
        self.bucket, self.prefixe = bucket, prefixe
        self._client = Minio(hote, access_key=cle_acces, secret_key=cle_secrete, secure=securise,
                             http_client=_client_http())

    def lister(self) -> list[str]:
        from minio.error import S3Error

        try:
            return [o.object_name for o in self._client.list_objects(self.bucket, prefix=self.prefixe or None,
                                                                     recursive=True)]
        except (S3Error, OSError, ValueError) as e:
            raise DepotIndisponible(f"bucket {self.bucket} : {e}") from e

    def lire(self, cle: str) -> bytes:
        from minio.error import S3Error

        if not cle.startswith(self.prefixe):
            raise DepotIndisponible(f"objet hors du préfixe configuré : {cle}")
        try:
            r = self._client.get_object(self.bucket, cle)
            try:
                return r.read()
            finally:
                r.close()
                r.release_conn()
        except (S3Error, OSError, ValueError) as e:
            raise DepotIndisponible(f"{cle} : {e}") from e


def _client_http():
    """Client urllib3 passant par HTTPS_PROXY s'il est défini (urllib3 ne le lit pas seul)."""
    proxy = os.getenv("HTTPS_PROXY") or os.getenv("https_proxy")
    if not proxy:
        return None
    import urllib3

    ca = os.getenv("REQUESTS_CA_BUNDLE") or os.getenv("SSL_CERT_FILE")
    return urllib3.ProxyManager(proxy, cert_reqs="CERT_REQUIRED", ca_certs=ca,
                                timeout=urllib3.Timeout(connect=10, read=60),
                                retries=urllib3.Retry(total=3, backoff_factor=0.5))


def depot_depuis_reglages(r: Reglages) -> DepotS3 | None:
    """None si le stockage n'est pas configuré (MINIO_ENDPOINT, MINIO_BUCKET, clés)."""
    acces, secret = r.minio_access_key.get_secret_value(), r.minio_secret_key.get_secret_value()
    if not (r.minio_endpoint and r.minio_bucket and acces and secret):
        return None
    return DepotS3(r.minio_endpoint, r.minio_bucket, r.minio_prefixe, acces, secret)


def documents_du_depot(depot: Depot) -> list[DocumentDetecte]:
    """Documents du bucket ; identifiants relatifs au préfixe configuré."""
    prefixe = depot.prefixe
    relatifs = [c[len(prefixe):] for c in depot.lister() if c.startswith(prefixe)]
    return constituer_documents(relatifs, vers_image=lambda c: uri(depot.bucket, prefixe + c))
