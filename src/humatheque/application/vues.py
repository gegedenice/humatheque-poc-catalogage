"""Modèles renvoyés par la couche de services (SPEC §9.1) : pages HTML et API
JSON `/api/v1` consomment les mêmes."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from humatheque.notice.validation import Marqueur
from humatheque.notice.versions import VersionNotice
from humatheque.spec.verificateur import Constat
from humatheque.store import Ingestion, Lot
from humatheque.workflow.modeles import Dossier

TypeDocument = Literal["these", "memoire"]


class EntreeLot(BaseModel):
    """Paramètres d'un envoi (SPEC §3) : ils s'appliquent à tous les documents du lot."""

    type_document: TypeDocument = "these"
    fournisseur: str | None = None
    documents: list[list[str]] = Field(description="un document = liste ordonnée d'images (URL ou chemins)")
    identifiants: list[str] | None = Field(None, description="identifiants des documents (chemin MinIO…)")
    source: str = "url"  # televersement | url | liste | minio
    declencheur: str = "manuel"


class EtatSpec(BaseModel):
    source: str
    emplacement: str
    version: str
    grist_joignable: bool | None = Field(None, description="None : mode CSV, Grist non interrogé")
    avertissements: list[str] = Field(default_factory=list)
    constats: list[Constat] = Field(default_factory=list)
    nb_erreurs: int = 0
    nb_avertissements: int = 0


class Indicateurs(BaseModel):
    documents: int = 0
    passes_seuls: int = 0
    en_attente: int = 0
    repris: int = 0
    en_cours: int = 0
    par_routage: dict[str, int] = Field(default_factory=dict)


class ResumeLot(BaseModel):
    lot: Lot
    indicateurs: Indicateurs
    en_cours: bool


class Accueil(BaseModel):
    spec: EtatSpec
    fournisseurs: list[str]
    fournisseur_defaut: str
    lots: list[ResumeLot]


class LigneException(BaseModel):
    """Une ligne de la file d'exceptions du lot (document, étape, motif)."""

    dossier_id: str
    document_id: str
    titre: str | None
    etape: str | None
    etape_libelle: str | None
    motif: str | None
    routage: str
    traite: bool = False
    repris: bool = False
    nb_exceptions: int = 0


class LigneJournal(BaseModel):
    horodatage: datetime | None = None
    texte: str


class VueLot(BaseModel):
    lot: Lot
    indicateurs: Indicateurs
    en_cours: bool
    filtre: str
    file: list[LigneException]
    journal: list[LigneJournal]
    etapes: list[dict[str, Any]] = Field(default_factory=list, description="avancement par étape")


class EtapeVue(BaseModel):
    nom: str
    libelle: str
    statut: str
    routage: str | None
    motif: str | None
    duree: float | None
    tentatives: int


class ChampVue(BaseModel):
    champ: str
    brut: Any
    normalise: Any


class ImageVue(BaseModel):
    url: str
    nom: str


class VueDossier(BaseModel):
    dossier: Dossier
    lot_id: str
    etat: str
    titre: str | None
    etapes: list[EtapeVue]
    images: list[ImageVue]
    champs: list[ChampVue]
    exceptions_ouvertes: int
    telechargements: dict[str, str] = Field(default_factory=dict, description="libellé → nom de fichier")


class Historique(BaseModel):
    ingestions: list[Ingestion]
    lots: list[ResumeLot]


class ResumeVersion(BaseModel):
    numero: int
    le: datetime
    auteur: str
    source: str
    statut: str
    diff: str


class VueNotice(BaseModel):
    """Notice éditable d'un dossier (SPEC §9.2)."""

    dossier_id: str
    profil: str | None
    version: int | None = Field(None, description="numéro de la version courante")
    statut: str | None = None
    source: str | None = None
    texte: str = ""
    marqueurs: list[Marqueur] = Field(default_factory=list)
    proposition: ResumeVersion | None = Field(None, description="régénération en attente (non reportée)")
    versions: list[ResumeVersion] = Field(default_factory=list)


def resume_version(v: VersionNotice) -> ResumeVersion:
    return ResumeVersion(numero=v.numero, le=v.le, auteur=v.auteur, source=v.source, statut=v.statut, diff=v.diff)
