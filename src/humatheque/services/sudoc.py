"""Client sudoc-checker (`/check/thesis`, `/check/dissertation`), SPEC §6 étape 4,
et lecture d'une notice Sudoc (`https://www.sudoc.fr/{ppn}.xml`, SPEC §7).

Contrat : docs/services/sudoc-checker.README.md. Payload repris de
`services.sudoc_request_payload` du POC. La qualification des candidats est
faite par `qualification/` (pas ici).
"""

from __future__ import annotations

from typing import Any

from humatheque.config import charger_yaml
from humatheque.services.base import Appel, ClientService, liste, texte


def _pipe(v: Any) -> str:
    """Champ personne multivalué → chaîne « A | B » (attendue par le service)."""
    return " | ".join(liste(v))


def payload_sudoc(m: dict[str, Any]) -> dict[str, Any]:
    p = {
        "title": texte(m.get("title")),
        "subtitle": texte(m.get("subtitle")),
        "author": texte(m.get("author")),
        "degree_type": texte(m.get("degree_type")),
        "discipline": texte(m.get("discipline")),
        "granting_institution": texte(m.get("granting_institution")),
        "co_tutelle_institutions": liste(m.get("co_tutelle_institutions")),
        "doctoral_school": texte(m.get("doctoral_school")),
        "defense_year": m.get("defense_year") or "",
        "advisor": _pipe(m.get("advisor")),
        "jury_president": texte(m.get("jury_president")),
        "reviewers": _pipe(m.get("reviewers")),
        "committee_members": _pipe(m.get("committee_members")),
        "language": texte(m.get("language")),
    }
    if m.get("confidence") is not None:
        p["confidence"] = m["confidence"]
    return p


def verifier_sudoc(client: ClientService, metadonnees: dict[str, Any], type_document: str) -> tuple[dict[str, Any], Appel]:
    route = charger_yaml("services")["sudoc"]["routes"][type_document]
    return client.post(route, payload_sudoc(metadonnees))


def lire_notice(client: ClientService, ppn: str) -> tuple[str, Appel]:
    """Notice UNIMARC XML d'un PPN (texte brut)."""
    return client.get_texte(charger_yaml("services")["sudoc"]["notice_xml"].format(ppn=ppn))


def lien_notice(ppn: str) -> str:
    return charger_yaml("services")["sudoc"]["lien_notice"].format(ppn=ppn)
