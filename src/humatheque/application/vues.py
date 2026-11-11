"""Modèles renvoyés par la couche de services (SPEC §9.1) : pages HTML et API
JSON `/api/v1` consomment les mêmes."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from humatheque.ingestion import DocumentDetecte
from humatheque.notice.validation import Marqueur
from humatheque.notice.versions import VersionNotice
from humatheque.regles.verificateur import Constat
from humatheque.store import Ingestion, Lot
from humatheque.workflow.modeles import Dossier

TypeDocument = Literal["these", "memoire"]


class EntreeLot(BaseModel):
    """Paramètres d'un envoi (SPEC §3) : ils s'appliquent à tous les documents du lot."""

    type_document: TypeDocument = "these"
    fournisseur: str | None = None
    documents: list[list[str]] = Field(description="un document = liste ordonnée d'images (URL ou chemins)")
    identifiants: list[str] | None = Field(None, description="identifiants des documents (chemin MinIO…)")
    types: list[TypeDocument] | None = Field(
        None, description="type par document (dossiers TH / MM du bucket) ; sinon type_document")
    source: str = "url"  # televersement | url | liste | minio | dossiers
    declencheur: str = "manuel"
    reglages_services: dict[str, dict[str, Any]] = Field(
        default_factory=dict, description="« Paramètres avancés » : {service: {paramètre: valeur}}")


class EtatSpec(BaseModel):
    """État des règles chargées (regles/) et rapport du vérificateur."""

    emplacement: str
    version: str
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


class Reglable(BaseModel):
    """Paramètre de service modifiable par lot (config/services.yaml, `reglables`)."""

    service: str
    parametre: str
    libelle: str
    defaut: Any
    min: float | None = None
    max: float | None = None
    choix: list[str] | None = None

    @property
    def nom(self) -> str:
        return f"reglage.{self.service}.{self.parametre}"


class Accueil(BaseModel):
    spec: EtatSpec
    fournisseurs: list[str]
    fournisseur_defaut: str
    lots: list[ResumeLot]
    reglables: list[Reglable] = Field(default_factory=list)


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
    passes: list[LigneException] = Field(default_factory=list, description="documents terminés en routage auto")


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
    parcours: list[dict] = Field(default_factory=list, description="étapes du pipeline : valeurs, chemin, résultat")
    alertes: list[Any] = Field(default_factory=list, description="groupes d'alertes à afficher (GroupeAlertes)")


class VueLots(BaseModel):
    """Liste des lots (page « Lots ») : filtres appliqués, lots à reprendre en tête."""
    lots: list[ResumeLot]
    a_reprendre: list[ResumeLot] = Field(default_factory=list, description="lots terminés avec des documents en attente, les plus chargés d'abord")
    etat: str = Field("", description="'' | en_cours | termines")
    declencheur: str = ""
    type_document: str = ""
    declencheurs: list[str] = Field(default_factory=list, description="déclencheurs présents (choix du filtre)")
    total: int = Field(0, description="nombre de lots avant filtrage")


class VueApercu(BaseModel):
    """Consultation en lecture seule d'un dossier : images et notice finale."""
    dossier_id: str
    lot_id: str
    document_id: str
    type_document: str
    titre: str | None
    routage: str
    situation: str | None = None
    motif_arret: str | None = None
    images: list[ImageVue]
    notice: str | None = Field(None, description="texte WinIBW de la notice finale")
    origine: str | None = Field(None, description="version validée / éditée / générée, ou notice convertie")
    profil: str | None = None
    alertes: list[Any] = Field(default_factory=list, description="groupes d'alertes à afficher (GroupeAlertes)")
    telechargements: dict[str, str] = Field(default_factory=dict)


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


class DossierBucket(BaseModel):
    """Un dossier du bucket et son dernier traitement."""

    document: DocumentDetecte
    nouveau: bool
    modifie: bool = Field(False, description="images changées depuis le dernier traitement")
    dossier_id: str | None = None
    lot_id: str | None = None
    etat: str | None = None
    routage: str | None = None


class VueBucket(BaseModel):
    configure: bool
    bucket: str | None = None
    prefixe: str | None = None
    erreur: str | None = None
    dossiers: list[DossierBucket] = Field(default_factory=list)
