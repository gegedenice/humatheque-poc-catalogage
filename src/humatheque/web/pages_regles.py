"""Pages d'administration des règles (SPEC §9.3, `/admin/regles`) : consultation,
banc d'essai des décisions, aperçu d'un profil, modification des fichiers YAML.
Aucune logique métier ici : tout passe par `application/regles_admin.py`."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from humatheque.application import ReglesRefusees
from humatheque.web.pages import COOKIE_AUTEUR, _app, _auteur, _rendre

routeur = APIRouter(include_in_schema=False)
ONGLETS = ("decisions", "profils", "variables", "modifier")


def _page(request: Request, onglet: str = "decisions", **contexte):
    a = _app(request)
    onglet = onglet if onglet in ONGLETS else "decisions"
    contexte.setdefault("fichier", a.vue_regles().fichiers[0])
    if onglet == "modifier" and "texte" not in contexte:
        contexte["texte"] = a.texte_regles(contexte["fichier"])
    from humatheque.regles.moteur import conventions

    return _rendre(request, "admin_regles.html", vue=a.vue_regles(), onglet=onglet,
                   dossiers=a.dossiers_pour_essai(), defauts=conventions()["completude"].get("fixes", {}), **contexte)


@routeur.get("/admin/regles")
def page_regles(request: Request, onglet: str = "decisions", fichier: str | None = None,
                profil: str | None = None, enregistre: str | None = None):
    contexte = {"profil": profil, "enregistre": enregistre}
    if fichier:
        contexte["fichier"] = fichier
    return _page(request, onglet, **contexte)


@routeur.post("/admin/regles/essai")
async def essai_decision(request: Request):
    """Banc d'essai : faits saisis (ou repris d'un dossier) → règle retenue et évaluations."""
    a = _app(request)
    f = await request.form()
    dossier = str(f.get("dossier") or "")
    faits = a.faits_dossier(dossier) if dossier else {k: str(f.get(k) or "") for k in a.regles.faits}
    return _rendre(request, "_regles_essai.html", choix=a.essayer_decision(faits),
                   source=f"dossier {dossier}" if dossier else "faits saisis")


@routeur.post("/admin/regles/apercu")
def apercu_profil(request: Request, profil: Annotated[str, Form()], dossier: Annotated[str, Form()] = ""):
    return _rendre(request, "_regles_apercu.html", apercu=_app(request).apercu_profil(profil, dossier or None))


@routeur.post("/admin/regles/verifier")
def verifier_brouillon(request: Request, fichier: Annotated[str, Form()], texte: Annotated[str, Form()]):
    return _rendre(request, "_regles_rapport.html", rapport=_app(request).verifier_brouillon(fichier, texte),
                   brouillon=True)


@routeur.post("/admin/regles/enregistrer")
def enregistrer(request: Request, fichier: Annotated[str, Form()], texte: Annotated[str, Form()],
                auteur: Annotated[str, Form()] = ""):
    a = _app(request)
    try:
        r = a.enregistrer_regles(fichier, texte, _auteur(auteur))
    except ReglesRefusees as e:
        reponse = _page(request, "modifier", fichier=fichier, texte=texte, refus=e.rapport)
        reponse.status_code = 422
        return reponse
    reponse = RedirectResponse(f"/admin/regles?onglet=modifier&fichier={fichier}&enregistre={r.archive}",
                               status_code=303)
    reponse.set_cookie(COOKIE_AUTEUR, auteur.strip(), max_age=3600 * 24 * 365, samesite="lax")
    return reponse
