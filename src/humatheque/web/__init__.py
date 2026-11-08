"""Application web (SPEC §9) : pages HTML rendues côté serveur (Jinja2 + HTMX)
et API JSON `/api/v1`. Les deux appellent les mêmes fonctions de
`humatheque.application` ; aucune logique métier ici."""

from __future__ import annotations

import re
from pathlib import Path

from markupsafe import Markup, escape

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from humatheque.application import ActionImpossible, Application, Conflit, Introuvable

ICI = Path(__file__).parent
gabarits = Jinja2Templates(directory=str(ICI / "templates"))

# Issue d'un document (humatheque.issues) → jeton de couleur et libellé affiché.
COULEURS_ROUTAGE = {"terminé": "auto", "à reprendre": "decision", "incident": "sortie"}
LIBELLES_ROUTAGE = {"terminé": "Terminé", "à reprendre": "À reprendre", "incident": "Incident"}


def _classe_routage(routage: str | None) -> str:
    from humatheque.issues import normaliser

    return COULEURS_ROUTAGE.get(normaliser(routage) or "", "sourdine")


def _colorer_notice(texte: str | None) -> Markup:
    """Notice WinIBW → lignes HTML colorées (zone, indicateurs, sous-zones), en lecture seule."""
    lignes = []
    for brute in (texte or "").splitlines():
        zone, reste = brute[:3], brute[4:] if len(brute) > 3 else ""
        ind = ""
        if len(reste) > 2 and reste[2] == "$":
            ind, reste = reste[:2], reste[2:]
        corps = re.sub(r"(\$.)", r'<span class="text-[#b3261e] font-semibold">\1</span>', str(escape(reste)))
        lignes.append(f'<span class="text-primaire font-semibold">{escape(zone)}</span> '
                      f'<span class="text-decision">{escape(ind)}</span>{corps}')
    return Markup("\n".join(lignes))


gabarits.env.filters["colorer_notice"] = _colorer_notice


def _survol_variables(html: Markup, infos: dict[str, dict]) -> Markup:
    """Variables `{nom}` / `{nom.attribut}` d'une ligne de profil : source et exemple au survol."""
    def remplacer(m: re.Match) -> str:
        nom = m.group(1)
        info = infos.get(nom)
        if not info:
            return m.group(0)
        titre = f"{nom} — source : {info.get('source') or '—'}"
        titre += f"\nexemple : {info['exemple']}" if info.get("exemple") else "\nexemple : (vide)"
        if not info.get("disponible", True):
            titre += "\nindisponible : jamais renseignée"
        return (f'<span class="underline decoration-dotted decoration-contour cursor-help" '
                f'title="{escape(titre)}">{m.group(0)}</span>')

    return Markup(re.sub(r"\{(\w+)(?:\.\w+)?\}", remplacer, str(html)))


gabarits.env.filters["survol_variables"] = _survol_variables


def _apercu_valeur(v) -> str:
    from humatheque.application.regles_admin import _apercu_valeur as apercu

    return apercu(v)


gabarits.env.filters["apercu_valeur"] = _apercu_valeur
def _libelle_routage(routage: str | None) -> str:
    from humatheque.issues import normaliser

    r = normaliser(routage)
    return LIBELLES_ROUTAGE.get(r or "", r or "—")


gabarits.env.globals.update(classe_routage=_classe_routage, libelle_routage=_libelle_routage)
gabarits.env.filters["date"] = lambda d, f="%d/%m %H:%M": d.astimezone().strftime(f) if d else ""


def create_app(application: Application | None = None, planificateur: bool = False) -> FastAPI:
    """`planificateur=True` démarre les déclencheurs automatiques (humatheque serve)."""
    from contextlib import asynccontextmanager

    from humatheque.application.planificateur import Planificateur

    a = application or Application()
    plan = Planificateur(a)

    @asynccontextmanager
    async def cycle(_app: FastAPI):
        if planificateur:
            plan.demarrer()
        yield
        plan.arreter()

    app = FastAPI(title="humatheque-catalogage", docs_url="/api/v1/docs", openapi_url="/api/v1/openapi.json",
                  lifespan=cycle)
    app.state.application = a
    app.state.planificateur = plan
    from humatheque.web.auth import Authentification

    app.add_middleware(Authentification)
    app.mount("/static", StaticFiles(directory=str(ICI / "static")), name="static")

    from humatheque.web import api, pages, pages_regles

    app.include_router(pages.routeur)
    app.include_router(pages_regles.routeur)
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
