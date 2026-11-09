"""Dossier d'un document : résultats de chaque étape, décisions, notice, journal (SPEC §6).

Le dossier est un JSON versionné (`schema_version`) ; il porte tout ce qui
permet de relancer le traitement sans rappeler les étapes réussies (ING-F-007)
et de tracer chaque transformation, appel, décision et zone produite.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, field_validator, Field

from humatheque.contexte import Contexte
from humatheque.conversion import NoticeConvertie
from humatheque.decision.moteur import Decision
from humatheque.decision.provenance import Provenance
from humatheque.extraction import ResultatExtraction
from humatheque.generateur.modeles import Notice
from humatheque.qualification import Qualification
from humatheque.services.alma import ResultatAlma
from humatheque.services.base import Appel
from humatheque.services.dewey import ResultatDewey
from humatheque.services.idref import AlignementIdref, AlignementOrganisations

SCHEMA_VERSION = 1

# Ordre des étapes (cf. mindmap, SPEC §6). Alma avant Sudoc (ALM-SUD-F-001).
ETAPES = [
    "extraction", "provenance", "alma", "sudoc", "situation", "conversion",
    "idref_personnes", "idref_organisations", "dewey", "generation",
]

# Statuts d'étape : a_faire, ok, ignoree, arret (décision du moteur ou exception
# à trancher), et les statuts d'incident du §6.
STATUTS_INCIDENT = {
    "json_invalide", "image_inaccessible", "inference_indisponible", "inference_timeout",
    "service_indisponible", "generation_echec", "conversion_echec",
}


def maintenant() -> datetime:
    return datetime.now(UTC)


def _issue(v):
    from humatheque.issues import normaliser

    return normaliser(v)


class Etape(BaseModel):
    nom: str
    statut: str = "a_faire"
    routage: str | None = Field(None, description="issue de l'étape (humatheque.issues)")
    motif: str | None = None
    requete: Any = None
    reponse: Any = None
    appels: list[Appel] = Field(default_factory=list)
    duree: float | None = None

    _norm_routage = field_validator("routage", mode="before")(classmethod(lambda cls, v: _issue(v)))
    erreur: str | None = None
    tentatives: int = 0
    debut: datetime | None = None
    fin: datetime | None = None

    @property
    def terminee(self) -> bool:
        return self.statut in ("ok", "ignoree")


class Evenement(BaseModel):
    horodatage: datetime = Field(default_factory=maintenant)
    etape: str
    type: str  # appel | transformation | decision | zone | resolution | alerte | relance
    detail: Any
    auteur: str = "système"


class ExceptionDossier(BaseModel):
    """Point à trancher par un catalogueur (file d'exceptions)."""

    etape: str
    objet: str
    motif: str
    candidats: list[Any] = Field(default_factory=list)
    resolue: bool = False


class Cloture(BaseModel):
    """Dossier traité hors outil (situation tranchée par le catalogueur, SPEC §9.3)."""

    par: str
    le: datetime = Field(default_factory=maintenant)
    commentaire: str


class Document(BaseModel):
    id: str
    type_document: str  # these | memoire
    images: list[str] = Field(default_factory=list, description="URL ou chemins, dans l'ordre")


class Dossier(BaseModel):
    schema_version: int = SCHEMA_VERSION
    id: str
    document: Document
    fournisseur_vlm: str | None = None
    version_spec: str | None = None
    cree_le: datetime = Field(default_factory=maintenant)

    etapes: dict[str, Etape] = Field(default_factory=lambda: {n: Etape(nom=n) for n in ETAPES})

    extraction: ResultatExtraction | None = None
    metadonnees: dict[str, Any] | None = None
    provenance: Provenance | None = None
    provenance_manuelle: str | None = None
    alma: ResultatAlma | None = None
    etat_alma: str | None = None
    sudoc: Qualification | None = None
    etat_sudoc: str | None = None
    corrections: dict[str, str] = Field(default_factory=dict, description="faux positifs déclarés (ALM-SUD-006)")
    decision: Decision | None = None
    notice_source_iso: str | None = Field(None, description="notice Sudoc source, ISO 2709 (ALM-SUD-F-002)")
    notice_source: str | None = Field(None, description="rendu WinIBW de la notice source")
    ppn_source: str | None = None
    notice_convertie: NoticeConvertie | None = None
    idref_personnes: dict[str, list[AlignementIdref | None]] = Field(default_factory=dict)
    idref_organisations: AlignementOrganisations | None = None
    dewey: ResultatDewey | None = None
    contexte: Contexte | None = None
    notice: Notice | None = None
    notice_texte: str | None = None

    alertes: list[str] = Field(default_factory=list)
    exceptions: list[ExceptionDossier] = Field(default_factory=list)
    journal: list[Evenement] = Field(default_factory=list)

    routage: str | None = Field(None, description="issue du dossier : terminé, à reprendre, incident")
    motif_arret: str | None = None
    etape_arret: str | None = None
    cloture: Cloture | None = None

    _norm_routage = field_validator("routage", mode="before")(classmethod(lambda cls, v: _issue(v)))

    def noter(self, etape: str, type_: str, detail: Any, auteur: str = "système") -> None:
        self.journal.append(Evenement(etape=etape, type=type_, detail=detail, auteur=auteur))

    def alerter(self, *alertes: str) -> None:
        for a in alertes:
            if a not in self.alertes:
                self.alertes.append(a)

    def reinitialiser_depuis(self, etape: str) -> None:
        """Remet à faire l'étape et les suivantes."""
        for n in ETAPES[ETAPES.index(etape):]:
            self.etapes[n] = Etape(nom=n)
        self.exceptions = [e for e in self.exceptions if ETAPES.index(e.etape) < ETAPES.index(etape)]
        self.routage = self.motif_arret = self.etape_arret = None
        if ETAPES.index(etape) <= ETAPES.index("sudoc"):  # la décision sera reprise par le pipeline
            self.decision = None
