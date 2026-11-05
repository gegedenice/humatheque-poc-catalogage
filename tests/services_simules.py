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


# ---------------------------------------------------------------- scénario complet
VLM = "https://vlm.test/v1/chat/completions"

EXTRACTION = {
    "title": "CONTRIBUTION A L'ETUDE DE LA DEMOCRATIE EN AFRIQUE",
    "subtitle": None,
    "author": "Jean-Daniel DUBOIS",
    "degree_type": "Thèse de doctorat",
    "discipline": "Science politique",
    "volume": None,
    "granting_institution": "École pratique des hautes études",
    "co_tutelle_institutions": [],
    "doctoral_school": None,
    "defense_year": "1998",
    "advisor": ["Pr Marie-France de LA ROCHE"],
    "jury_president": None,
    "reviewers": ["Luc PETIT"],
    "committee_members": [],
    "language": "fre",
    "abstract": None,
    "confidence": 0.9,
}


def png() -> bytes:
    from io import BytesIO

    from PIL import Image

    buf = BytesIO()
    Image.new("RGB", (100, 140), "white").save(buf, format="PNG")
    return buf.getvalue()


def services(spec, fournisseur=None):
    """Services du workflow pointant vers les URL simulées."""
    from humatheque.extraction.fournisseurs import FournisseurOpenAICompatible
    from humatheque.services import ClientService
    from humatheque.workflow import Services

    return Services(
        spec=spec,
        vlm=FournisseurOpenAICompatible(fournisseur or "vlm", VLM, "google/gemma-4-26B-A4B-it", timeout=5),
        alma=ClientService("alma-checker", ALMA_URL),
        sudoc=ClientService("sudoc-checker", SUDOC_URL),
        idref=ClientService("idref-linker", IDREF_URL),
        dewey=ClientService("dewey-classifier", DEWEY_URL),
        seuil_sudoc=0.78,
    )


def _orgs_acceptees() -> dict:
    o = copy.deepcopy(idref_organisations())
    o["co_tutelle_institutions"] = []
    o["doctoral_school"] = None
    return o


def simuler(extraction=None, alma=None, sudoc=None, orgs=None, idref_statut="accepted"):
    """Monte les routes respx de tous les services (dans un contexte respx actif)."""
    import respx

    contenu = json.dumps(EXTRACTION if extraction is None else extraction, ensure_ascii=False)
    return {
        "vlm": respx.post(VLM).respond(json={"choices": [{"message": {"content": contenu}}]}),
        "alma": respx.post(url__regex=rf"{ALMA_URL}/check/.*").respond(json=alma or globals()["alma"]()),
        "sudoc": respx.post(url__regex=rf"{SUDOC_URL}/check/.*").respond(json=sudoc or globals()["sudoc"]([])),
        "idref": respx.post(f"{IDREF_URL}/align/person").respond(json=idref_personne(idref_statut)),
        "orgs": respx.post(f"{IDREF_URL}/align/thesis-organizations").respond(
            json=orgs if orgs is not None else _orgs_acceptees()),
        "dewey": respx.post(f"{DEWEY_URL}/classify").respond(json=dewey()),
        "notice": respx.get(url__regex=r"https://www\.sudoc\.fr/\d+X?\.xml").respond(text=notice_xml()),
    }
