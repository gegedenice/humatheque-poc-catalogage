"""Mot de passe de l'application (étape 8) : désactivé tant que MOT_DE_PASSE est vide.

Pages : cookie de session signé (HMAC du mot de passe) posé par /login.
API JSON : en-tête `Authorization: Bearer <mot de passe>` ou le même cookie.
Toujours ouverts : /login, /static, /minio/evenements (protégé par son propre jeton).
"""

from __future__ import annotations

import hashlib
import hmac

from fastapi import Request
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware

COOKIE = "humatheque_session"
OUVERTS = ("/login", "/static/", "/minio/evenements")


def jeton_session(mot_de_passe: str) -> str:
    return hmac.new(mot_de_passe.encode(), b"humatheque-session", hashlib.sha256).hexdigest()


def authentifie(request: Request, mot_de_passe: str) -> bool:
    attendu = jeton_session(mot_de_passe)
    if hmac.compare_digest(request.cookies.get(COOKIE, ""), attendu):
        return True
    entete = request.headers.get("Authorization", "")
    return entete.startswith("Bearer ") and hmac.compare_digest(entete[7:], mot_de_passe)


class Authentification(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        mot_de_passe = request.app.state.application.reglages.mot_de_passe.get_secret_value()
        chemin = request.url.path
        if not mot_de_passe or chemin.startswith(OUVERTS) or authentifie(request, mot_de_passe):
            return await call_next(request)
        if chemin.startswith("/api/"):
            return JSONResponse({"detail": "authentification requise"}, status_code=401)
        if request.headers.get("HX-Request"):
            r = JSONResponse({"detail": "authentification requise"}, status_code=401)
            r.headers["HX-Redirect"] = "/login"
            return r
        return RedirectResponse(f"/login?suite={chemin}", status_code=303)
