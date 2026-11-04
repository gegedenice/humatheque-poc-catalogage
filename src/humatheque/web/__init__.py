"""Application web (SPEC §9) : pages HTML rendues côté serveur (Jinja2 + HTMX)
et API JSON `/api/v1`. Les deux appellent les mêmes fonctions de
`humatheque.application` ; aucune logique métier ici."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from humatheque.application import ActionImpossible, Application, Conflit, Introuvable

ICI = Path(__file__).parent
gabarits = Jinja2Templates(directory=str(ICI / "templates"))

COULEURS_ROUTAGE = {"auto": "auto", "décision": "decision", "sortie": "sortie", "conversion": "conversion",
                    "incident": "incident"}
LIBELLES_ROUTAGE = {"auto": "auto", "décision": "décision", "sortie": "sortie du pipeline",
                    "conversion": "conversion", "incident": "incident"}


def _classe_routage(routage: str | None) -> str:
    return COULEURS_ROUTAGE.get(routage or "", "sourdine")


gabarits.env.globals.update(classe_routage=_classe_routage, libelle_routage=lambda r: LIBELLES_ROUTAGE.get(r or "", r or "—"))
gabarits.env.filters["date"] = lambda d, f="%d/%m %H:%M": d.astimezone().strftime(f) if d else ""


def create_app(application: Application | None = None) -> FastAPI:
    app = FastAPI(title="humatheque-catalogage", docs_url="/api/v1/docs", openapi_url="/api/v1/openapi.json")
    app.state.application = application or Application()
    app.mount("/static", StaticFiles(directory=str(ICI / "static")), name="static")

    from humatheque.web import api, pages

    app.include_router(pages.routeur)
    app.include_router(api.routeur, prefix="/api/v1")

    @app.exception_handler(Introuvable)
    async def _introuvable(request: Request, exc: Introuvable):
        if request.url.path.startswith("/api/"):
            return JSONResponse({"detail": f"introuvable : {exc}"}, status_code=404)
        return gabarits.TemplateResponse(request, "erreur.html", {"message": f"Introuvable : {exc}"},
                                         status_code=404)

    @app.exception_handler(Conflit)
    async def _conflit(request: Request, exc: Conflit):
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.exception_handler(ActionImpossible)
    async def _impossible(request: Request, exc: ActionImpossible):
        if request.url.path.startswith("/api/"):
            details = [d.model_dump() if hasattr(d, "model_dump") else d for d in exc.details]
            return JSONResponse({"detail": str(exc), "marqueurs": details}, status_code=422)
        # Requête HTMX : le texte est affiché par le bandeau d'erreur global.
        if request.headers.get("HX-Request"):
            return HTMLResponse(str(exc), status_code=422)
        return gabarits.TemplateResponse(request, "erreur.html", {"message": str(exc)}, status_code=422)

    return app
