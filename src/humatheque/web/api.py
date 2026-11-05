"""API JSON `/api/v1` (SPEC §9.1) : mêmes fonctions de `application/` que les pages."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from humatheque.application import Application
from humatheque.application.vues import (
    EntreeLot, EtatSpec, Historique, Reglable, ResumeVersion, VueBucket, VueDossier, VueLot, VueNotice,
)
from humatheque.notice.validation import Marqueur
from humatheque.store import Lot

routeur = APIRouter(tags=["api"])


def _app(request: Request) -> Application:
    return request.app.state.application


@routeur.get("/spec", response_model=EtatSpec)
def spec(request: Request) -> EtatSpec:
    return _app(request).etat_spec()


@routeur.post("/spec/recharger", response_model=EtatSpec)
def recharger_spec(request: Request) -> EtatSpec:
    return _app(request).recharger_spec()


@routeur.post("/lots", response_model=Lot, status_code=201)
def creer_lot(request: Request, entree: EntreeLot) -> Lot:
    return _app(request).creer_lot(entree)


@routeur.get("/lots/{lot_id}", response_model=VueLot)
def lot(request: Request, lot_id: str, filtre: str = "tous") -> VueLot:
    return _app(request).vue_lot(lot_id, filtre)


@routeur.get("/lots/{lot_id}/rapport.csv", response_class=PlainTextResponse)
def rapport(request: Request, lot_id: str) -> str:
    return _app(request).export_csv(lot_id)


@routeur.get("/lots/{lot_id}/notices.txt", response_class=PlainTextResponse)
def notices(request: Request, lot_id: str) -> str:
    return _app(request).export_notices(lot_id)


@routeur.get("/dossiers/{dossier_id}", response_model=VueDossier)
def dossier(request: Request, dossier_id: str) -> VueDossier:
    return _app(request).vue_dossier(dossier_id)


class Relance(BaseModel):
    depuis: str | None = None


@routeur.post("/dossiers/{dossier_id}/relancer", response_model=VueDossier)
def relancer(request: Request, dossier_id: str, corps: Relance | None = None) -> VueDossier:
    return _app(request).relancer(dossier_id, corps.depuis if corps else None)


class Resolution(BaseModel):
    auteur: str
    commentaire: str = ""
    provenance: str | None = None
    service: Literal["alma", "sudoc"] | None = None
    exception: int | None = None
    ppn: str | None = None
    code: str | None = None


@routeur.post("/dossiers/{dossier_id}/resolutions/{type_}", response_model=VueDossier)
def resoudre(request: Request, dossier_id: str, type_: Literal["provenance", "faux-positif", "ppn", "dewey",
                                                              "hors-outil"], r: Resolution) -> VueDossier:
    a = _app(request)
    if type_ == "provenance":
        return a.choisir_provenance(dossier_id, r.provenance or "", r.auteur, r.commentaire)
    if type_ == "faux-positif":
        return a.declarer_faux_positif(dossier_id, r.service or "", r.commentaire, r.auteur)
    if type_ == "ppn":
        return a.choisir_ppn(dossier_id, r.exception if r.exception is not None else -1, r.ppn, r.auteur)
    if type_ == "dewey":
        return a.choisir_dewey(dossier_id, r.exception if r.exception is not None else -1, r.code or "", r.auteur)
    return a.traiter_hors_outil(dossier_id, r.commentaire, r.auteur)


@routeur.get("/historique", response_model=Historique)
def historique(request: Request) -> Historique:
    return _app(request).historique()


# ---------------------------------------------------------------- notice éditable (SPEC §9.2)
class TexteNotice(BaseModel):
    texte: str


class EnregistrementNotice(BaseModel):
    texte: str
    auteur: str
    version: int = Field(description="version courante sur laquelle porte l'édition")


class Auteur(BaseModel):
    auteur: str


@routeur.get("/notices/{dossier_id}", response_model=VueNotice)
def notice(request: Request, dossier_id: str) -> VueNotice:
    return _app(request).vue_notice(dossier_id)


@routeur.put("/notices/{dossier_id}", response_model=VueNotice)
def enregistrer_notice(request: Request, dossier_id: str, corps: EnregistrementNotice) -> VueNotice:
    return _app(request).enregistrer_notice(dossier_id, corps.texte, corps.auteur, corps.version)


@routeur.get("/notices/{dossier_id}/versions", response_model=list[ResumeVersion])
def versions(request: Request, dossier_id: str) -> list[ResumeVersion]:
    return _app(request).vue_notice(dossier_id).versions


@routeur.post("/notices/{dossier_id}/validation", response_model=list[Marqueur])
def validation(request: Request, dossier_id: str, corps: TexteNotice) -> list[Marqueur]:
    return _app(request).valider_texte(dossier_id, corps.texte)


@routeur.post("/notices/{dossier_id}/valider", response_model=VueNotice)
def valider(request: Request, dossier_id: str, corps: Auteur) -> VueNotice:
    return _app(request).valider_notice(dossier_id, corps.auteur)


@routeur.post("/notices/{dossier_id}/propositions/{numero}/{choix}", response_model=VueNotice)
def proposition(request: Request, dossier_id: str, numero: int, choix: Literal["reporter", "ignorer"],
                corps: Auteur) -> VueNotice:
    a = _app(request)
    if choix == "reporter":
        return a.reporter_proposition(dossier_id, numero, corps.auteur)
    return a.ignorer_proposition(dossier_id, numero, corps.auteur)



# ---------------------------------------------------------------- stockage S3 (SPEC §3.1)
class Selection(BaseModel):
    identifiants: list[str]
    fournisseur: str | None = None
    reglages_services: dict[str, dict] = {}


@routeur.get("/reglables", response_model=list[Reglable])
def reglables(request: Request) -> list[Reglable]:
    return _app(request).reglables()


@routeur.post("/minio/balayage", response_model=VueBucket)
def balayage(request: Request) -> VueBucket:
    return _app(request).balayer("manuel")


@routeur.post("/minio/lots", response_model=Lot, status_code=201)
def lancer_selection(request: Request, selection: Selection) -> Lot:
    return _app(request).lancer_selection(selection.identifiants, selection.fournisseur,
                                          reglages=selection.reglages_services)
