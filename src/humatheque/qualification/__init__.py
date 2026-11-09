"""Agrégation des candidats de sudoc-checker (SPEC §6 étape 4) : le `thesis_state`
du document est celui du candidat retenu (score ≥ seuil) le plus fort, dans
l'ordre `ORDRE` ; un `unknown` retenu l'emporte sauf si une originale imprimée
est retenue (on ne tranche pas à la place du catalogueur). Les codes de l'API
sont gardés tels quels. Fonction pure, sans appel réseau.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field



class CandidatSudoc(BaseModel):
    ppn: str | None
    score: float | None
    carrier: str | None = None
    thesis_state: str | None = None
    etat: str | None = Field(None, description="thesis_state reconnu (None : non listé)")
    retenu: bool
    titre: str | None = None
    auteurs: list[str] = Field(default_factory=list)
    annee: str | None = None
    type: str | None = None
    lien: str | None = None


class Qualification(BaseModel):
    etat: str | None = Field(None, description="thesis_state retenu pour le document (None : aucun candidat)")
    justification: str
    incident: bool = False
    statut_service: str | None = None
    seuil: float
    candidats: list[CandidatSudoc] = Field(default_factory=list)
    ppn_source: str | None = Field(None, description="meilleur candidat retenu ayant déterminé l'état")
    code: str | None = Field(None, description="code de l'API qui a déterminé l'état (thesis_state ou status)")


# Ordre de force des thesis_state (le premier l'emporte) ; INCONNU : non qualifiable.
ORDRE = ["printed_original", "printed_reproduction", "microfiche_reproduction", "electronic"]
INCONNU = "unknown"


def agreger(reponse: dict[str, Any], seuil: float) -> Qualification:
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
            etat=c["thesis_state"] if c["thesis_state"] in ORDRE else None,
            retenu=score is not None and score >= seuil,
            titre=c.get("title"), auteurs=c.get("authors") or [], annee=c.get("year"),
            type=these.get("type"), lien=c.get("url"),
        ))

    retenus = [c for c in candidats if c.retenu]
    base = dict(statut_service=statut, seuil=seuil, candidats=candidats)
    if not retenus:
        return Qualification(etat=None, code=statut, justification=f"aucun candidat au-dessus du seuil {seuil} "
                             f"(statut du service : {statut})", **base)

    ordre: list[str] = ORDRE
    connus = [c for c in retenus if c.etat in ordre]
    plus_fort = min((ordre.index(c.etat) for c in connus), default=None)
    inconnus = [c for c in retenus if c.etat is None]
    if inconnus and plus_fort != 0:
        ppns = ", ".join(str(c.ppn) for c in inconnus)
        return Qualification(etat=INCONNU, code=str(inconnus[0].thesis_state), justification=f"candidat(s) retenu(s) non qualifiable(s) "
                             f"(thesis_state {', '.join(sorted({str(c.thesis_state) for c in inconnus}))}) : {ppns}",
                             **base)
    etat = ordre[plus_fort]
    source = max((c for c in connus if c.etat == etat), key=lambda c: c.score or 0)
    justif = f"{len(retenus)} candidat(s) retenu(s) ; état le plus fort : {etat} ({source.ppn}, score {source.score})"
    if inconnus:
        justif += " ; candidat(s) non qualifiable(s) ignoré(s) car une originale imprimée est retenue"
    return Qualification(etat=etat, justification=justif, ppn_source=source.ppn, code=source.thesis_state, **base)
