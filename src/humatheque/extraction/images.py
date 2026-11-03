"""Préparation des images pour le VLM (repris de `providers.compress_image_to_data_url`
du POC) : réduction du plus grand côté, JPEG, URI data base64."""

from __future__ import annotations

import base64
from io import BytesIO

import httpx
from PIL import Image, UnidentifiedImageError


class ImageInaccessible(Exception):
    """Image introuvable ou injoignable (statut `image_inaccessible`, relance)."""


class ImageIllisible(ImageInaccessible):
    """Image lue mais non décodable : document invalide (ING-F-008, routage décision)."""


def compresser(donnees: bytes, max_size: int = 768, qualite: int = 85) -> str:
    try:
        img = Image.open(BytesIO(donnees))
        img.load()
    except (UnidentifiedImageError, OSError) as e:
        raise ImageIllisible(f"image illisible : {e}") from e
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    w, h = img.size
    ratio = min(max_size / w, max_size / h, 1.0)
    if ratio < 1:
        img = img.resize((int(w * ratio), int(h * ratio)), Image.LANCZOS)
    buf = BytesIO()
    img.save(buf, format="JPEG", quality=qualite, optimize=True)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def lire(source: str | bytes, timeout: float = 30.0, client: httpx.Client | None = None) -> bytes:
    """Octets d'une image : bytes, chemin local ou URL http(s)."""
    if isinstance(source, bytes):
        return source
    if source.startswith(("http://", "https://")):
        try:
            r = (client or httpx).get(source, timeout=timeout, follow_redirects=True)
            r.raise_for_status()
            return r.content
        except httpx.HTTPError as e:
            raise ImageInaccessible(f"{source} : {e}") from e
    from pathlib import Path

    chemin = Path(source)
    if not chemin.is_file():
        raise ImageInaccessible(f"fichier introuvable : {source}")
    return chemin.read_bytes()
