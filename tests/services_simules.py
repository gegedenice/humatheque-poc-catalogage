"""Réponses des services pour les mocks respx, dérivées des fixtures réelles."""

from __future__ import annotations

import copy
import json
from pathlib import Path

DOSSIER = Path(__file__).parent / "fixtures" / "services"

ALMA_URL = "https://alma.test"
SUDOC_URL = "https://sudoc.test"
IDREF_URL = "https://idref.test"
DEWEY_URL = "https://dewey.test"


def fixture(nom: str) -> dict:
    return json.loads((DOSSIER / f"{nom}.json").read_text(encoding="utf-8"))


def notice_xml() -> str:
    return (DOSSIER / "sudoc-229969437.xml").read_text(encoding="utf-8")


def alma(statut: str = "no_academic_record_found", ppn: str | None = None) -> dict:
    r = fixture("alma")
    r["status"] = statut
    if statut == "no_academic_record_found":
        r["candidates"], r["best_academic_candidate"] = [], None
        r["decision_support"] = {"best_candidate_ppn": None, "best_candidate_has_ppn": False, "ppns": []}
    elif ppn:
        r["decision_support"]["best_candidate_ppn"] = ppn
    return r


def sudoc(candidats: list[tuple[str, float, str | None]], statut: str = "no_print_duplicate_found") -> dict:
    """candidats : (ppn, score final, thesis_state ; None = clé absente)."""
    r = fixture("sudoc")
    modele = r["candidates"][0]
    r["status"] = statut
    r["candidates"] = []
    for ppn, score, etat in candidats:
        c = copy.deepcopy(modele)
        c["ppn"], c["url"] = ppn, f"https://www.sudoc.fr/{ppn}"
        c["score"]["final"] = score
        if etat is None:
            c.pop("thesis_state", None)
        else:
            c["thesis_state"] = etat
            c["carrier"] = "electronic" if etat == "electronic" else "printed_or_physical"
        r["candidates"].append(c)
    return r


def idref_personne(statut: str = "accepted", ppn: str | None = "026940345") -> dict:
    r = fixture("idref")
    r["status"] = statut
    r["best_ppn"] = ppn if statut == "accepted" else None
    return r


def idref_organisations() -> dict:
    return fixture("idreforg")


def dewey() -> dict:
    return fixture("dewey")
