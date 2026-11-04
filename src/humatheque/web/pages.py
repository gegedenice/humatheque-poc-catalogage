"""Pages HTML (SPEC §9.3) et fragments HTMX. Chaque route appelle une fonction
de `application/` et rend un gabarit ; aucune logique métier ici."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse, Response

from humatheque.application import ActionImpossible, Application, documents_depuis_liste
from humatheque.application.vues import EntreeLot
from humatheque.web import gabarits

routeur = APIRouter(include_in_schema=False)
COOKIE_AUTEUR = "catalogueur"


def _app(request: Request) -> Application:
    return request.app.state.application


def _rendre(request: Request, gabarit: str, **contexte):
    contexte.setdefault("auteur", request.cookies.get(COOKIE_AUTEUR, ""))
    return gabarits.TemplateResponse(request, gabarit, contexte)


def _rediriger(url: str, auteur: str | None = None) -> RedirectResponse:
    r = RedirectResponse(url, status_code=303)
    if auteur:
        r.set_cookie(COOKIE_AUTEUR, auteur, max_age=3600 * 24 * 365, samesite="lax")
    return r


# ---------------------------------------------------------------- accueil
@routeur.get("/")
def accueil(request: Request):
    return _rendre(request, "accueil.html", vue=_app(request).accueil())


@routeur.post("/spec/recharger")
def recharger_spec(request: Request):
    etat = _app(request).recharger_spec()
    if request.headers.get("HX-Request"):
        return _rendre(request, "_spec.html", spec=etat)
    return _rediriger("/")


@routeur.post("/lots")
async def creer_lot(
    request: Request,
    type_document: Annotated[str, Form()] = "these",
    fournisseur: Annotated[str, Form()] = "",
    url: Annotated[str, Form()] = "",
    liste_urls: Annotated[str, Form()] = "",
    un_seul_document: Annotated[bool, Form()] = False,
    images: Annotated[list[UploadFile] | None, File()] = None,
    dossiers: Annotated[list[UploadFile] | None, File()] = None,
):
    a = _app(request)
    # Téléversement d'une arborescence (input webkitdirectory : nom = chemin relatif)
    arborescence = [(f.filename or "", await f.read()) for f in (dossiers or []) if f.filename]
    if arborescence:
        if type_document not in ("these", "memoire"):
            raise ActionImpossible(f"type de document inconnu : {type_document}")
        lot = a.televerser_dossiers(arborescence, type_document, fournisseur or None)
        return _rediriger(f"/lots/{lot.id}")
    fichiers = [(f.filename or "image", await f.read()) for f in (images or []) if f.filename]
    if fichiers:
        documents, source = a.enregistrer_televersement(fichiers, un_seul_document), "televersement"
    elif url.strip():
        documents, source = [[url.strip()]], "url"
    elif liste_urls.strip():
        documents, source = documents_depuis_liste(liste_urls), "liste"
    else:
        raise ActionImpossible("Indiquez des images : téléversement, URL ou liste d'URL.")
    if type_document not in ("these", "memoire"):
        raise ActionImpossible(f"type de document inconnu : {type_document}")
    lot = a.creer_lot(EntreeLot(type_document=type_document, fournisseur=fournisseur or None,
                                documents=documents, source=source))
    if lot.mode == 1:
        (ligne,) = a.store.lignes_du_lot(lot.id)
        return _rediriger(f"/dossiers/{ligne.id}")
    return _rediriger(f"/lots/{lot.id}")


# ---------------------------------------------------------------- lot (mode 2)
@routeur.get("/lots/{lot_id}")
def lot(request: Request, lot_id: str, filtre: str = "tous"):
    return _rendre(request, "lot.html", vue=_app(request).vue_lot(lot_id, filtre))


@routeur.get("/lots/{lot_id}/fragment")
def lot_fragment(request: Request, lot_id: str, filtre: str = "tous"):
    """File d'exceptions + journal, indicateurs et bandeau en hx-swap-oob."""
    return _rendre(request, "_lot_fragment.html", vue=_app(request).vue_lot(lot_id, filtre), oob=True)


@routeur.get("/lots/{lot_id}/rapport.csv")
def rapport(request: Request, lot_id: str):
    return Response(_app(request).export_csv(lot_id), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="rapport-{lot_id}.csv"'})


@routeur.get("/lots/{lot_id}/notices.txt")
def notices(request: Request, lot_id: str):
    return Response(_app(request).export_notices(lot_id), media_type="text/plain; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="notices-{lot_id}.txt"'})


# ---------------------------------------------------------------- dossier (mode 1)
@routeur.get("/dossiers/{dossier_id}")
def dossier(request: Request, dossier_id: str):
    a = _app(request)
    return _rendre(request, "dossier.html", vue=a.vue_dossier(dossier_id), notice=a.vue_notice(dossier_id))


@routeur.get("/dossiers/{dossier_id}/fragment")
def dossier_fragment(request: Request, dossier_id: str):
    """Panneau droit du dossier, rafraîchi tant que le traitement est en cours ; à la fin,
    rechargement complet pour monter l'éditeur (îlot hors fragment rafraîchi)."""
    vue = _app(request).vue_dossier(dossier_id)
    reponse = _rendre(request, "_dossier_panneau.html", vue=vue)
    if vue.etat == "termine":
        reponse.headers["HX-Refresh"] = "true"
    return reponse


@routeur.get("/dossiers/{dossier_id}/versions")
def versions_notice(request: Request, dossier_id: str):
    """Versions de la notice et régénération en attente (rafraîchi après chaque enregistrement)."""
    return _rendre(request, "_notice_versions.html", notice=_app(request).vue_notice(dossier_id),
                   dossier_id=dossier_id)


@routeur.post("/dossiers/{dossier_id}/propositions/{numero}")
def proposition(request: Request, dossier_id: str, numero: int, choix: Annotated[str, Form()],
                auteur: Annotated[str, Form()]):
    a = _app(request)
    if choix == "reporter":
        a.reporter_proposition(dossier_id, numero, _auteur(auteur))
    else:
        a.ignorer_proposition(dossier_id, numero, _auteur(auteur))
    return _rediriger(f"/dossiers/{dossier_id}", auteur)


@routeur.get("/dossiers/{dossier_id}/telecharger/{fichier}")
def telecharger(request: Request, dossier_id: str, fichier: str):
    nom, contenu, mime = _app(request).telecharger(dossier_id, fichier)
    return Response(contenu, media_type=mime, headers={"Content-Disposition": f'attachment; filename="{nom}"'})


@routeur.post("/dossiers/{dossier_id}/relancer")
def relancer(request: Request, dossier_id: str, depuis: Annotated[str, Form()] = ""):
    _app(request).relancer(dossier_id, depuis or None)
    return _rediriger(f"/dossiers/{dossier_id}")


@routeur.post("/dossiers/{dossier_id}/provenance")
def provenance(request: Request, dossier_id: str, valeur: Annotated[str, Form()],
               auteur: Annotated[str, Form()], commentaire: Annotated[str, Form()] = ""):
    _app(request).choisir_provenance(dossier_id, valeur, _auteur(auteur), commentaire)
    return _rediriger(f"/dossiers/{dossier_id}", auteur)


@routeur.post("/dossiers/{dossier_id}/faux-positif")
def faux_positif(request: Request, dossier_id: str, service: Annotated[str, Form()],
                 auteur: Annotated[str, Form()], commentaire: Annotated[str, Form()] = ""):
    _app(request).declarer_faux_positif(dossier_id, service, commentaire, _auteur(auteur))
    return _rediriger(f"/dossiers/{dossier_id}", auteur)


@routeur.post("/dossiers/{dossier_id}/ppn")
def ppn(request: Request, dossier_id: str, exception: Annotated[int, Form()], auteur: Annotated[str, Form()],
        ppn: Annotated[str, Form()] = "", saisie: Annotated[str, Form()] = ""):
    choix = (saisie or ppn).strip()
    _app(request).choisir_ppn(dossier_id, exception, None if choix in ("", "sans-lien") else choix, _auteur(auteur))
    return _rediriger(f"/dossiers/{dossier_id}", auteur)


@routeur.post("/dossiers/{dossier_id}/dewey")
def dewey(request: Request, dossier_id: str, exception: Annotated[int, Form()], code: Annotated[str, Form()],
          auteur: Annotated[str, Form()]):
    _app(request).choisir_dewey(dossier_id, exception, code, _auteur(auteur))
    return _rediriger(f"/dossiers/{dossier_id}", auteur)


@routeur.post("/dossiers/{dossier_id}/hors-outil")
def hors_outil(request: Request, dossier_id: str, auteur: Annotated[str, Form()],
               commentaire: Annotated[str, Form()] = ""):
    _app(request).traiter_hors_outil(dossier_id, commentaire, _auteur(auteur))
    return _rediriger(f"/dossiers/{dossier_id}", auteur)


def _auteur(auteur: str) -> str:
    if not auteur.strip():
        raise ActionImpossible("Indiquez votre nom (catalogueur) pour journaliser la résolution.")
    return auteur.strip()


# ---------------------------------------------------------------- historique, fichiers
@routeur.get("/historique")
def historique(request: Request):
    return _rendre(request, "historique.html", vue=_app(request).historique())


@routeur.get("/fichiers/{chemin:path}")
def fichier(request: Request, chemin: str):
    return FileResponse(_app(request).chemin_fichier(chemin))


# ---------------------------------------------------------------- stockage S3 (SPEC §3.1)
@routeur.post("/minio/balayer")
def balayer(request: Request):
    """Liste les dossiers du bucket et leur dernier statut (balayage journalisé)."""
    return _rendre(request, "_minio.html", vue=_app(request).balayer("manuel"),
                   fournisseurs=_app(request).accueil().fournisseurs)


@routeur.post("/minio/lancer")
def lancer_selection(request: Request, dossiers: Annotated[list[str] | None, Form()] = None,
                     fournisseur: Annotated[str, Form()] = ""):
    lot = _app(request).lancer_selection(dossiers or [], fournisseur or None)
    if request.headers.get("HX-Request"):
        r = Response(status_code=204)
        r.headers["HX-Redirect"] = f"/lots/{lot.id}"
        return r
    return _rediriger(f"/lots/{lot.id}")


@routeur.get("/minio/objet")
def objet_s3(request: Request, uri: str):
    contenu, mime = _app(request).objet_s3(uri)
    return Response(contenu, media_type=mime, headers={"Cache-Control": "private, max-age=3600"})


@routeur.post("/minio/evenements")
def evenements_s3(request: Request):
    """Notifications d'évènements S3 (ING-F-003) : architecture prévue, désactivée tant que
    INGESTION_AUTO=false (arbitrage en attente, SPEC §11 étape 8)."""
    a = _app(request)
    if not a.reglages.ingestion_auto:
        return Response("Ingestion automatique désactivée (INGESTION_AUTO=false).", status_code=403)
    lot = a.traiter_nouveaux("evenement")
    return Response(lot.id if lot else "aucun nouveau dossier", status_code=202)
