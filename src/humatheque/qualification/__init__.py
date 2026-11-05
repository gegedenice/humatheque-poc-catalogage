"""Agrégation des thesis_state de sudoc-checker en état du document (SPEC §6 étape 4).

Fonction pure, sans appel réseau ; correspondances dans config/etats_sudoc.yaml.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from humatheque.config import charger_yaml


class CandidatSudoc(BaseModel):
    ppn: str | None
    score: float | None
    carrier: str | None = None
    thesis_state: str | None = None
    etat: str | None = Field(None, description="etat_sudoc correspondant")
    retenu: bool
    titre: str | None = None
    auteurs: list[str] = Field(default_factory=list)
    annee: str | None = None
    type: str | None = None
    lien: str | None = None


class Qualification(BaseModel):
    etat: str | None = Field(None, description="None : incident")
    justification: str
    incident: bool = False
    statut_service: str | None = None
    seuil: float
    candidats: list[CandidatSudoc] = Field(default_factory=list)
    ppn_source: str | None = Field(None, description="meilleur candidat retenu ayant déterminé l'état")


def agreger(reponse: dict[str, Any], seuil: float) -> Qualification:
    cfg = charger_yaml("etats_sudoc")
    statut = reponse.get("status")
    bruts = reponse.get("candidates")
    if bruts is None or not isinstance(bruts, list):
        return Qualification(incident=True, justification="réponse sans liste de candidats",
                             statut_service=statut, seuil=seuil)
    candidats: list[CandidatSudoc] = []
    for c in bruts:
        if "thesis_state" not in c:
            return Qualification(incident=True, statut_service=statut, seuil=seuil,
                                 justification="thesis_state absent de la réponse (version du service antérieure)")
        score = (c.get("score") or {}).get("final")
        these = c.get("thesis") or {}
        candidats.append(CandidatSudoc(
            ppn=c.get("ppn"), score=score, carrier=c.get("carrier"), thesis_state=c["thesis_state"],
            etat=cfg["correspondance"].get(c["thesis_state"]),
            retenu=score is not None and score >= seuil,
            titre=c.get("title"), auteurs=c.get("authors") or [], annee=c.get("year"),
            type=these.get("type"), lien=c.get("url"),
        ))

    retenus = [c for c in candidats if c.retenu]
    base = dict(statut_service=statut, seuil=seuil, candidats=candidats)
    if not retenus:
        etat = cfg["etat_ambigu"] if statut == "ambiguous_print_candidate" else cfg["etat_rien"]
        return Qualification(etat=etat, justification=f"aucun candidat au-dessus du seuil {seuil} "
                             f"(statut du service : {statut})", **base)

    ordre: list[str] = cfg["ordre"]
    connus = [c for c in retenus if c.etat in ordre]
    plus_fort = min((ordre.index(c.etat) for c in connus), default=None)
    inconnus = [c for c in retenus if c.etat is None]
    if inconnus and plus_fort != 0:
        ppns = ", ".join(str(c.ppn) for c in inconnus)
        return Qualification(etat=cfg["etat_inconnu"], justification=f"candidat(s) retenu(s) non qualifiable(s) "
                             f"(thesis_state {', '.join(sorted({str(c.thesis_state) for c in inconnus}))}) : {ppns}",
                             **base)
    etat = ordre[plus_fort]
    source = max((c for c in connus if c.etat == etat), key=lambda c: c.score or 0)
    justif = f"{len(retenus)} candidat(s) retenu(s) ; état le plus fort : {etat} ({source.ppn}, score {source.score})"
    if inconnus:
        justif += " ; candidat(s) non qualifiable(s) ignoré(s) car une originale imprimée est retenue"
    return Qualification(etat=etat, justification=justif, ppn_source=source.ppn, **base)
