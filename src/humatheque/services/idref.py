"""Client idref-linker : `/align/person` et `/align/thesis-organizations` (SPEC §6 étape 6).

Contrat : docs/services/idref-linker.README.md et .openapi.json (réponses
relevées dans tests/fixtures/services). Statuts : accepted, ambiguous,
low_confidence, not_found ; le PPN n'est renseigné que pour accepted.
Paramètres de score envoyés depuis config/services.yaml (réglables par lot).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from humatheque.config import charger_yaml
from humatheque.services.base import Appel, ClientService, liste, texte


class CandidatIdref(BaseModel):
    ppn: str | None = None
    libelle: str | None = None
    score: float | None = None
    url: str | None = None


class AlignementIdref(BaseModel):
    """Alignement d'une personne ou d'un organisme."""

    statut: str
    ppn: str | None = Field(None, description="PPN retenu par le service (accepted)")
    libelle: str | None = None
    candidats: list[CandidatIdref] = Field(default_factory=list)
    erreur: str | None = None


def _contexte_these(m: dict[str, Any]) -> dict[str, str]:
    return {
        "title": texte(m.get("title")),
        "subtitle": texte(m.get("subtitle")),
        "discipline": texte(m.get("discipline")),
        "institution": texte(m.get("granting_institution")),
        "doctoral_school": texte(m.get("doctoral_school")),
        "degree_type": texte(m.get("degree_type")),
        "year": texte(m.get("defense_year")),
    }


def payload_personne(m: dict[str, Any], nom_brut: str) -> dict[str, Any]:
    """`name` seul : le service découpe lui-même ; `first_name` / `last_name` sont
    des surcharges réservées aux découpages incertains (README d'idref-linker)."""
    return {"name": nom_brut, **_contexte_these(m)}


def payload_organisations(m: dict[str, Any]) -> dict[str, Any]:
    return {
        "granting_institution": texte(m.get("granting_institution")),
        "co_tutelle_institutions": liste(m.get("co_tutelle_institutions")),
        "doctoral_school": texte(m.get("doctoral_school")),
        "year": texte(m.get("defense_year")),
    }


def _personne(r: dict[str, Any]) -> AlignementIdref:
    cands = [CandidatIdref(ppn=c.get("ppn"),
                           libelle=" ".join(filter(None, [c.get("last_name"), c.get("first_name")])) or None,
                           score=(c.get("score") or {}).get("final"), url=c.get("url"))
             for c in r.get("candidates") or []]
    return AlignementIdref(statut=str(r.get("status") or ""), ppn=r.get("best_ppn"), candidats=cands,
                           erreur=r.get("error"))


def _organisme(r: dict[str, Any] | None) -> AlignementIdref | None:
    if not r:
        return None
    cands = [CandidatIdref(ppn=c.get("ppn"), libelle=c.get("label_officiel"), score=c.get("score"), url=c.get("url"))
             for c in r.get("candidates") or []]
    return AlignementIdref(statut=str(r.get("status") or ""), ppn=r.get("ppn"), libelle=r.get("label_officiel"),
                           candidats=cands, erreur=r.get("error"))


def aligner_personne(client: ClientService, m: dict[str, Any], nom_brut: str,
                     parametres: dict[str, Any] | None = None) -> tuple[AlignementIdref, Appel]:
    route = charger_yaml("services")["idref_personne"]["route"]
    reponse, appel = client.post(route, {**payload_personne(m, nom_brut), **(parametres or {})})
    return _personne(reponse), appel


class AlignementOrganisations(BaseModel):
    granting_institution: AlignementIdref | None = None
    co_tutelle_institutions: list[AlignementIdref] = Field(default_factory=list)
    doctoral_school: AlignementIdref | None = None


def aligner_organisations(client: ClientService, m: dict[str, Any],
                          parametres: dict[str, Any] | None = None) -> tuple[AlignementOrganisations, Appel]:
    route = charger_yaml("services")["idref_organisations"]["route"]
    r, appel = client.post(route, {**payload_organisations(m), **(parametres or {})})
    return AlignementOrganisations(
        granting_institution=_organisme(r.get("granting_institution")),
        co_tutelle_institutions=[a for a in (_organisme(x) for x in r.get("co_tutelle_institutions") or []) if a],
        doctoral_school=_organisme(r.get("doctoral_school")),
    ), appel
