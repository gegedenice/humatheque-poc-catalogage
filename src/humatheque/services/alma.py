"""Client alma-checker (`/check/thesis`, `/check/dissertation`), SPEC §6 étape 3.

Contrat : docs/services/alma-checker.README.md et .openapi.json.
L'état Alma vient de `config/etats_alma.yaml` ; le PPN de `decision_support`
est toujours repris.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from humatheque.config import charger_yaml
from humatheque.services.base import Appel, ClientService, liste, texte


class CandidatAlma(BaseModel):
    ppn: str | None = None
    mms_id: str | None = None
    titre: str | None = None
    auteurs: list[str] = Field(default_factory=list)
    annee: str | None = None
    type: str | None = None
    score: float | None = None
    lien_sudoc: str | None = None
    dans_le_profil: bool | None = None


class ResultatAlma(BaseModel):
    statut: str
    etat: str | None = Field(None, description="présent | absent ; None si statut inconnu")
    motif: str | None = None
    ppn: str | None = Field(None, description="decision_support.best_candidate_ppn")
    candidats: list[CandidatAlma] = Field(default_factory=list)


def payload_alma(m: dict[str, Any]) -> dict[str, Any]:
    p = {
        "title": texte(m.get("title")),
        "subtitle": texte(m.get("subtitle")),
        "author": texte(m.get("author")),
        "degree_type": texte(m.get("degree_type")),
        "discipline": texte(m.get("discipline")),
        "volume": texte(m.get("volume")),
        "granting_institution": texte(m.get("granting_institution")),
        "co_tutelle_institutions": liste(m.get("co_tutelle_institutions")),
        "doctoral_school": texte(m.get("doctoral_school")),
        "defense_year": m.get("defense_year") or None,
        "advisor": liste(m.get("advisor")),
        "jury_president": texte(m.get("jury_president")),
        "reviewers": liste(m.get("reviewers")),
        "committee_members": liste(m.get("committee_members")),
        "language": texte(m.get("language")),
    }
    if m.get("confidence") is not None:
        p["confidence"] = m["confidence"]
    return p


def _candidat(c: dict[str, Any]) -> CandidatAlma:
    these = c.get("thesis") or {}
    academique = c.get("academic") or {}
    return CandidatAlma(
        ppn=c.get("ppn"), mms_id=c.get("mms_id"), titre=c.get("title"), auteurs=c.get("authors") or [],
        annee=c.get("year"), type=these.get("type") or academique.get("kind"),
        score=(c.get("score") or {}).get("final"), lien_sudoc=c.get("sudoc_url"),
        dans_le_profil=c.get("counts_for_profile"),
    )


def interpreter(reponse: dict[str, Any]) -> ResultatAlma:
    cfg = charger_yaml("etats_alma")
    statut = str(reponse.get("status") or "")
    return ResultatAlma(
        statut=statut,
        etat=cfg["correspondance"].get(statut),
        motif=(cfg.get("motifs") or {}).get(statut),
        ppn=(reponse.get("decision_support") or {}).get("best_candidate_ppn"),
        candidats=[_candidat(c) for c in reponse.get("candidates") or []],
    )


def verifier_alma(client: ClientService, metadonnees: dict[str, Any], type_document: str) -> tuple[ResultatAlma, Appel]:
    route = charger_yaml("services")["alma"]["routes"][type_document]
    reponse, appel = client.post(route, payload_alma(metadonnees))
    return interpreter(reponse), appel
