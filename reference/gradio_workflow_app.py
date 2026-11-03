from __future__ import annotations

import json
import os
import tempfile
import base64
from io import BytesIO
from pathlib import Path
from typing import Any

import gradio as gr
import requests
from dotenv import load_dotenv
from PIL import Image

SCRIPT_DIR = Path(__file__).resolve().parent
ENV_PATHS = (SCRIPT_DIR.parent / ".env", SCRIPT_DIR / ".env", Path.cwd() / ".env")
for env_path in ENV_PATHS:
    load_dotenv(env_path, override=False)


DEFAULT_ALMA_URL = os.getenv("ALMA_CHECKER_API_URL", "https://alma-checker.smartbiblia.fr")
DEFAULT_ALMA_API_KEY = os.getenv("ALMA_API_KEY", "")
DEFAULT_SUDOC_URL = os.getenv("SUDOC_CHECKER_API_URL", "https://sudoc-checker.smartbiblia.fr")
DEFAULT_HF_URL = os.getenv("HF_API_URL", "https://router.huggingface.co/v1/chat/completions")
DEFAULT_HF_MODEL = os.getenv("HF_MODEL", "google/gemma-4-26B-A4B-it:deepinfra")
DEFAULT_IDREF_URL = os.getenv("IDREF_QUALINKA_API_URL", "https://idref-linker.smartbiblia.fr")
DEFAULT_IDREF_API_KEY = os.getenv("IDREF_QUALINKA_API_KEY", "")
DEFAULT_DEWEY_URL = os.getenv("DEWEY_CLASSIFICATION_API_URL", "https://dewey-classifier.smartbiblia.fr")
DEFAULT_DEWEY_API_KEY = os.getenv("CLASSIFICATION_API_KEY", "")
EMBEDDING_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"

METADATA_FIELDS = [
    "title",
    "subtitle",
    "author",
    "degree_type",
    "discipline",
    "volume",
    "granting_institution",
    "co_tutelle_institutions",
    "doctoral_school",
    "defense_year",
    "advisor",
    "jury_president",
    "reviewers",
    "committee_members",
    "language",
    "confidence",
]
MULTIVALUED_FIELDS = ["co_tutelle_institutions", "advisor", "reviewers", "committee_members"]
PERSON_FIELDS = ["author", "advisor", "jury_president", "reviewers", "committee_members"]
ROLE_LABELS = {
    "author": "Auteur",
    "advisor": "Directeur",
    "jury_president": "President du jury",
    "reviewers": "Rapporteur",
    "committee_members": "Membre du jury",
}


def hf_vlm_extraction(
    image: Any,
    prompt: str,
    endpoint: str = DEFAULT_HF_URL,
    model: str = DEFAULT_HF_MODEL,
) -> str:
    """Extract metadata with Hugging Face Router's OpenAI-compatible API."""
    api_key = os.getenv("HF_TOKEN", "").strip()
    if not api_key:
        checked = ", ".join(str(path) for path in ENV_PATHS)
        raise ValueError(f"HF_TOKEN is missing from the environment. Checked: {checked}")
    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": _image_to_data_url(image)}},
                ],
            }
        ],
        "response_format": {"type": "json_object"},
    }
    response = requests.post(
        endpoint,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        json=payload,
        timeout=None,
    )
    response.raise_for_status()
    raw = response.json()
    content = (((raw.get("choices") or [{}])[0]).get("message") or {}).get("content")
    return _pretty_json(normalize_metadata(_json_object(content)))


def alma_verification(
    metadata: str,
    doc_type: str = "memoire",
    api_url: str = DEFAULT_ALMA_URL,
    api_key: str = DEFAULT_ALMA_API_KEY,
) -> str:
    """Check whether the thesis/dissertation already exists in the Humatheque Alma catalogue."""
    corrected = normalize_metadata(_json_object(metadata))
    route = "/check/dissertation" if _doc_type(doc_type) == "memoire" else "/check/thesis"
    payload = {
        "title": _text(corrected.get("title")),
        "subtitle": _text(corrected.get("subtitle")),
        "author": _text(corrected.get("author")),
        "degree_type": _text(corrected.get("degree_type")),
        "discipline": _text(corrected.get("discipline")),
        "volume": _text(corrected.get("volume")),
        "granting_institution": _text(corrected.get("granting_institution")),
        "co_tutelle_institutions": corrected.get("co_tutelle_institutions") or [],
        "doctoral_school": _text(corrected.get("doctoral_school")),
        "defense_year": corrected.get("defense_year") or "",
        "advisor": corrected.get("advisor") or [],
        "jury_president": _text(corrected.get("jury_president")),
        "reviewers": corrected.get("reviewers") or [],
        "committee_members": corrected.get("committee_members") or [],
        "language": _text(corrected.get("language")),
    }
    if corrected.get("confidence") is not None:
        payload["confidence"] = corrected.get("confidence")
    headers = {"Content-Type": "application/json"}
    if api_key.strip():
        headers["X-API-Key"] = api_key.strip()
    # Errors are reported, not raised: downstream blocks get their metadata through this output.
    try:
        response = requests.post(f"{api_url.rstrip('/')}{route}", headers=headers, json=payload, timeout=None)
        response.raise_for_status()
        result = response.json()
    except requests.RequestException as exc:
        result = {"status": "error", "error": str(exc)}
    return _pretty_json({"route": route, "request": payload, "response": result, "context": {"metadata": corrected}})


def sudoc_verification(
    alma_result: str,
    doc_type: str = "memoire",
    api_url: str = DEFAULT_SUDOC_URL,
) -> str:
    """Check whether the thesis/dissertation already exists in Sudoc.

    The metadata is read from the Alma Verification output context.
    """
    context, alma = _split_context(alma_result)
    corrected = normalize_metadata(context.get("metadata") or {})
    route = "/check/dissertation" if _doc_type(doc_type) == "memoire" else "/check/thesis"
    payload = {
        "title": _text(corrected.get("title")),
        "subtitle": _text(corrected.get("subtitle")),
        "author": _text(corrected.get("author")),
        "degree_type": _text(corrected.get("degree_type")),
        "discipline": _text(corrected.get("discipline")),
        "granting_institution": _text(corrected.get("granting_institution")),
        "co_tutelle_institutions": corrected.get("co_tutelle_institutions") or [],
        "doctoral_school": _text(corrected.get("doctoral_school")),
        "defense_year": corrected.get("defense_year") or "",
        "advisor": _pipe(corrected.get("advisor")),
        "jury_president": _text(corrected.get("jury_president")),
        "reviewers": _pipe(corrected.get("reviewers")),
        "committee_members": _pipe(corrected.get("committee_members")),
        "language": _text(corrected.get("language")),
    }
    if corrected.get("confidence") is not None:
        payload["confidence"] = corrected.get("confidence")
    # Errors are reported, not raised: downstream blocks get their metadata through this output.
    try:
        response = requests.post(f"{api_url.rstrip('/')}{route}", json=payload, timeout=None)
        response.raise_for_status()
        result = response.json()
    except requests.RequestException as exc:
        result = {"status": "error", "error": str(exc)}
    context = {**context, "alma": alma}
    return _pretty_json({"route": route, "request": payload, "response": result, "context": context})


def idref_alignment(
    sudoc_result: str,
    api_url: str = DEFAULT_IDREF_URL,
    api_key: str = DEFAULT_IDREF_API_KEY,
    use_embeddings: bool = False,
    max_candidates: int = 20,
    max_docs_per_role: int = 20,
    reference_top_k: int = 3,
    accept_threshold: float = 0.65,
    margin_threshold: float = 0.08,
) -> str:
    """Align all extracted people against IdRef through the Qualinka API.

    The metadata is read from the Sudoc Verification output context.
    """
    context = _forward_context(sudoc_result)
    corrected = normalize_metadata(context.get("metadata") or {})
    headers = {"Content-Type": "application/json"}
    if api_key.strip():
        headers["X-API-Key"] = api_key.strip()

    persons = []
    for person in extract_people(corrected):
        payload = {
            "name": person["name"],
            "title": _text(corrected.get("title")),
            "subtitle": _text(corrected.get("subtitle")),
            "discipline": _text(corrected.get("discipline")),
            "institution": _text(corrected.get("granting_institution")),
            "doctoral_school": _text(corrected.get("doctoral_school")),
            "degree_type": _text(corrected.get("degree_type")),
            "year": _text(corrected.get("defense_year")),
            "max_candidates": int(max_candidates),
            "max_docs_per_role": int(max_docs_per_role),
            "reference_top_k": int(reference_top_k),
            "accept_threshold": float(accept_threshold),
            "margin_threshold": float(margin_threshold),
            "embedding_model": EMBEDDING_MODEL if use_embeddings else "",
            "weight_name": 0.40,
            "weight_attrra_source": 0.25,
            "weight_attrra_note": 0.15,
            "weight_references": 0.15,
            "weight_institution_year": 0.05,
        }
        try:
            response = requests.post(f"{api_url.rstrip('/')}/align/person", headers=headers, json=payload, timeout=None)
            response.raise_for_status()
            result = response.json()
        except requests.RequestException as exc:
            result = {"status": "error", "error": str(exc), "candidates": []}
        persons.append({"name": person["name"], "roles": person["roles"], "request": payload, "response": result})
    return _pretty_json({"persons": persons, "context": context})


def organization_alignment(
    sudoc_result: str,
    api_url: str = DEFAULT_IDREF_URL,
    api_key: str = DEFAULT_IDREF_API_KEY,
    allow_remote: bool = False,
    max_candidates: int = 5,
) -> str:
    """Align granting institution, co-tutelles and doctoral school to IdRef PPNs.

    The metadata is read from the Sudoc Verification output context.
    """
    context = _forward_context(sudoc_result)
    corrected = normalize_metadata(context.get("metadata") or {})
    payload = {
        "granting_institution": _text(corrected.get("granting_institution")),
        "co_tutelle_institutions": corrected.get("co_tutelle_institutions") or [],
        "doctoral_school": _text(corrected.get("doctoral_school")),
        "defense_year": _text(corrected.get("defense_year")),
        "allow_remote": bool(allow_remote),
        "max_candidates": int(max_candidates),
    }
    headers = {"Content-Type": "application/json"}
    if api_key.strip():
        headers["X-API-Key"] = api_key.strip()
    response = requests.post(f"{api_url.rstrip('/')}/align/thesis-organizations", headers=headers, json=payload, timeout=None)
    response.raise_for_status()
    return _pretty_json({"request": payload, "response": response.json(), "context": context})


def dewey_classification(
    sudoc_result: str,
    api_url: str = DEFAULT_DEWEY_URL,
    api_key: str = DEFAULT_DEWEY_API_KEY,
    top_k: int = 5,
    threshold: float = 0.20,
    method: str = "local",
) -> str:
    """Classify the work with the Dewey classifier and select the first class.

    The metadata is read from the Sudoc Verification output context.
    """
    context = _forward_context(sudoc_result)
    corrected = normalize_metadata(context.get("metadata") or {})
    text = " ".join(
        part for part in [
            _text(corrected.get("title")),
            _text(corrected.get("subtitle")),
            _text(corrected.get("discipline")),
        ]
        if part
    )
    if not text:
        raise ValueError("Title, subtitle, and discipline are empty; Dewey classification cannot run.")
    payload = {
        "text": text,
        "classification_type": "multi-label",
        "top_k": int(top_k),
        "threshold": float(threshold),
        "method": method,
    }
    headers = {"Content-Type": "application/json"}
    if api_key.strip():
        headers["X-API-Key"] = api_key.strip()
    response = requests.post(f"{api_url.rstrip('/')}/classify", headers=headers, json=payload, timeout=None)
    response.raise_for_status()
    result = response.json()
    classes = (((result.get("results") or [{}])[0]).get("classes")) or []
    selected = _selected_dewey(classes[0]) if classes else None
    return _pretty_json({"request": payload, "response": result, "classes": classes, "selected": selected, "context": context})


def unimarc_draft(
    idref_result: str = "",
    organization_result: str = "",
    dewey_result: str = "",
    doc_type: str = "memoire",
) -> str:
    """Build the final consolidated JSON draft and pseudo-UNIMARC preview.

    Metadata, Alma and Sudoc results are read from the context carried by the upstream outputs.
    """
    context_idref, idref = _split_context(idref_result)
    context_organizations, organizations = _split_context(organization_result)
    context_dewey, dewey = _split_context(dewey_result)
    context = context_idref or context_organizations or context_dewey
    corrected = normalize_metadata(context.get("metadata") or {})
    sudoc = context.get("sudoc") or {}
    alma = context.get("alma") or {}
    draft = build_draft(corrected, sudoc, idref, organizations, dewey, doc_type, alma)
    return json.dumps(draft, ensure_ascii=False, indent=2) + "\n\n```unimarc\n" + pseudo_unimarc(draft) + "\n```"


def build_draft(
    metadata: dict[str, Any],
    sudoc_result: dict[str, Any],
    idref_result: dict[str, Any],
    organization_result: dict[str, Any],
    dewey_result: dict[str, Any],
    doc_type: str,
    alma_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    alma_result = alma_result or {}
    alma_response = alma_result.get("response") or alma_result
    alma_best = alma_response.get("best_academic_candidate") or {}
    sudoc_response = sudoc_result.get("response") or sudoc_result
    organization_response = organization_result.get("response") or organization_result
    persons = []
    for item in idref_result.get("persons") or []:
        response = item.get("response") or {}
        best = _best_idref_candidate(response)
        status = response.get("status")
        ppn = _candidate_ppn(response, best)
        persons.append(
            {
                "name": item.get("name"),
                "roles": item.get("roles") or [],
                "role_labels": [ROLE_LABELS.get(role, role) for role in item.get("roles") or []],
                "idref_ppn": ppn,
                "idref_url": best.get("url"),
                "status": status,
                "score": (best.get("score") or {}).get("final"),
                "strong_idref": bool(ppn),
            }
        )
    return {
        "profile": "memoire_original_imprime" if _doc_type(doc_type) == "memoire" else "these_originale_imprimee",
        "work_metadata": {
            "title": metadata.get("title"),
            "subtitle": metadata.get("subtitle"),
            "language": metadata.get("language") or "fre",
            "defense_year": metadata.get("defense_year"),
            "volume": metadata.get("volume"),
        },
        "thesis_metadata": {
            "degree_type": metadata.get("degree_type"),
            "discipline": metadata.get("discipline"),
            "granting_institution": metadata.get("granting_institution"),
            "co_tutelle_institutions": metadata.get("co_tutelle_institutions") or [],
            "doctoral_school": metadata.get("doctoral_school"),
        },
        "persons": persons,
        "dewey_classification": dewey_result.get("selected"),
        "organization_alignments": organization_response,
        "alma_check": {
            "status": alma_response.get("status"),
            "match_score": alma_response.get("match_score"),
            "mms_id": alma_best.get("mms_id"),
            "ppn": alma_best.get("ppn"),
            "sudoc_url": alma_best.get("sudoc_url"),
            "items_count": alma_best.get("items_count"),
        },
        "sudoc_check": {
            "status": sudoc_response.get("status"),
            "duplicate_score": sudoc_response.get("duplicate_score"),
            "printed_duplicate_ppn": (sudoc_response.get("best_print_candidate") or {}).get("ppn"),
            "best_electronic_ppn": (sudoc_response.get("best_electronic_candidate") or {}).get("ppn"),
        },
    }


def pseudo_unimarc(draft: dict[str, Any]) -> str:
    work = draft.get("work_metadata") or {}
    thesis = draft.get("thesis_metadata") or {}
    dewey = draft.get("dewey_classification") or {}
    author = _first_person(draft, "author")
    advisors = _people_with_role(draft, "advisor")
    year = _text(work.get("defense_year"))
    language = _text(work.get("language")) or "fre"
    lines = [
        "001  [à attribuer]",
        "008  Aax3",
        "029  ##$aFR",
        f"100  ##$a{year if year else 'uuuu'}    k  y0{language}y50      ba",
        f"101  0#$a{language}",
        "102  ##$aFR",
        "106  ##$ar",
        "181  ##$P01$ctxt",
        "182  ##$P01$cn",
        "183  ##$P01$anga",
        f"200  1#$a{work.get('title') or ''}{_sf('e', work.get('subtitle'))}{_sf('f', author.get('name') if author else '')}{_sf('g', _advisor_statement(advisors))}",
        f"214  #1$d{year}",
        "215  ##$a1 volume" if not work.get("volume") else "215  ##$a? volumes",
        f"328  #0$b{thesis.get('degree_type') or ''}$c{thesis.get('discipline') or ''}$e{thesis.get('granting_institution') or ''}$d{year}",
        "608  ##$3027253139$aThèses et écrits académiques$2rameau",
    ]
    if dewey.get("code"):
        lines.append(f"686  ##$a{dewey.get('code')}$2TEF")
    for person in draft.get("persons") or []:
        tag = "700" if "author" in (person.get("roles") or []) else "701"
        lines.append(f"{tag}  #1{_ppn(person)}{_name_subfields(person.get('name'))}{_role_codes(person, draft.get('profile'))}")
    for corporate in corporate_entries(draft):
        lines.append(f"711  02{_ppn(corporate)}$a{corporate['name']}{corporate.get('role_code', '')}")
    return "\n".join(lines)


def corporate_entries(draft: dict[str, Any]) -> list[dict[str, Any]]:
    thesis = draft.get("thesis_metadata") or {}
    alignments = draft.get("organization_alignments") or {}
    entries = []
    _add_corporate(entries, thesis.get("granting_institution"), alignments.get("granting_institution"), "$4295")
    for index, name in enumerate(thesis.get("co_tutelle_institutions") or []):
        alignment = ((alignments.get("co_tutelle_institutions") or []) + [None] * (index + 1))[index]
        _add_corporate(entries, name, alignment, "$4995")
    _add_corporate(entries, thesis.get("doctoral_school"), alignments.get("doctoral_school"), "$4996")
    return entries


def _add_corporate(entries: list[dict[str, Any]], name: Any, alignment: Any, role_code: str) -> None:
    label = _text(name)
    if not label:
        return
    result = alignment if isinstance(alignment, dict) else {}
    candidate = _best_org_candidate(result)
    ppn = _org_ppn(result, candidate)
    entries.append(
        {
            "name": label,
            "idref_ppn": ppn,
            "strong_idref": bool(ppn),
            "role_code": role_code,
            "alignment_status": result.get("status"),
            "alignment_label": result.get("label_officiel") or candidate.get("label_officiel"),
            "score": result.get("score") or candidate.get("score"),
        }
    )


def normalize_metadata(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = {field: payload.get(field) for field in METADATA_FIELDS}
    for field in MULTIVALUED_FIELDS:
        normalized[field] = _multi(normalized.get(field))
    for field in ["author", "jury_president"]:
        value = normalized.get(field)
        if isinstance(value, list):
            normalized[field] = next((str(item).strip() for item in value if str(item).strip()), None)
    if normalized.get("volume") is not None:
        normalized["volume"] = str(normalized["volume"]).strip() or None
    return normalized


def extract_people(metadata: dict[str, Any]) -> list[dict[str, Any]]:
    people: dict[str, dict[str, Any]] = {}
    for role in PERSON_FIELDS:
        for name in _multi(metadata.get(role)):
            key = " ".join(name.lower().split())
            people.setdefault(key, {"name": name, "roles": []})
            if role not in people[key]["roles"]:
                people[key]["roles"].append(role)
    return list(people.values())


def _json_object(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if not isinstance(value, str):
        return json.loads(json.dumps(value))
    text = value.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if "\n" in text:
            text = text.split("\n", 1)[1]
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end >= start:
        text = text[start : end + 1]
    return json.loads(text)


def _split_context(result: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    """Split a block output into the context carried down the pipeline and the block's own result."""
    parsed = dict(_json_object(result)) if result else {}
    return parsed.pop("context", None) or {}, parsed


def _forward_context(sudoc_result: Any) -> dict[str, Any]:
    """Context for the blocks after Sudoc Verification: upstream context plus the Sudoc result."""
    context, sudoc = _split_context(sudoc_result)
    return {**context, "sudoc": sudoc}


def _pretty_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)


def _image_to_data_url(image: Any, max_size: int = 768) -> str:
    if image is None:
        raise ValueError("Image is required.")
    if isinstance(image, dict):
        image = image.get("path") or image.get("name") or image.get("url") or image
    if isinstance(image, str) and image.startswith("data:"):
        return image
    if isinstance(image, str) and image.startswith(("http://", "https://")):
        response = requests.get(image, timeout=10)
        response.raise_for_status()
        data = response.content
    elif isinstance(image, (str, Path)):
        data = Path(image).read_bytes()
    elif isinstance(image, bytes):
        data = image
    else:
        raise ValueError(f"Unsupported image input: {type(image).__name__}")

    img = Image.open(BytesIO(data))
    img.load()
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    ratio = min(max_size / img.width, max_size / img.height, 1.0)
    if ratio < 1:
        img = img.resize((int(img.width * ratio), int(img.height * ratio)), Image.LANCZOS)
    buffer = BytesIO()
    img.save(buffer, format="JPEG", quality=85, optimize=True)
    return "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def _doc_type(value: str) -> str:
    return "memoire" if str(value or "").strip().lower() == "memoire" else "these"


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _multi(value: Any) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [part.strip() for part in str(value).split("|") if part.strip()]


def _pipe(value: Any) -> str:
    return " | ".join(_multi(value))


def _selected_dewey(cls: dict[str, Any]) -> dict[str, Any]:
    return {"code": cls.get("dewey"), "label": cls.get("label"), "score": cls.get("score")}


def _best_idref_candidate(response: dict[str, Any]) -> dict[str, Any]:
    candidates = [candidate for candidate in response.get("candidates") or [] if isinstance(candidate, dict)]
    if candidates:
        return max(candidates, key=_candidate_score)
    return response.get("best_candidate") or {}


def _candidate_score(candidate: dict[str, Any]) -> float:
    score = candidate.get("score")
    if isinstance(score, dict):
        score = score.get("final")
    try:
        return float(score)
    except (TypeError, ValueError):
        return -1.0


def _candidate_ppn(response: dict[str, Any], candidate: dict[str, Any]) -> str | None:
    for value in (candidate.get("ppn"), response.get("best_ppn"), _ppn_from_url(candidate.get("url"))):
        text = _text(value)
        if text:
            return text
    return None


def _best_org_candidate(response: dict[str, Any]) -> dict[str, Any]:
    candidates = [candidate for candidate in response.get("candidates") or [] if isinstance(candidate, dict)]
    if candidates:
        return max(candidates, key=lambda candidate: _safe_float(candidate.get("score")))
    redirect = response.get("redirect")
    return redirect if isinstance(redirect, dict) else {}


def _org_ppn(response: dict[str, Any], candidate: dict[str, Any]) -> str | None:
    redirect = response.get("redirect")
    redirect_ppn = redirect.get("ppn") if isinstance(redirect, dict) else None
    for value in (response.get("ppn"), redirect_ppn, candidate.get("ppn"), _ppn_from_url(candidate.get("url"))):
        text = _text(value)
        if text:
            return text
    return None


def _safe_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return -1.0


def _ppn_from_url(url: Any) -> str:
    text = _text(url).rstrip("/")
    return text.rsplit("/", 1)[-1] if "idref.fr/" in text else ""


def _people_with_role(draft: dict[str, Any], role: str) -> list[dict[str, Any]]:
    return [person for person in draft.get("persons") or [] if role in (person.get("roles") or [])]


def _first_person(draft: dict[str, Any], role: str) -> dict[str, Any] | None:
    people = _people_with_role(draft, role)
    return people[0] if people else None


def _advisor_statement(advisors: list[dict[str, Any]]) -> str:
    names = [person.get("name") for person in advisors if person.get("name")]
    return "sous la direction de " + " ; ".join(names) if names else ""


def _sf(code: str, value: Any) -> str:
    return f"${code}{value}" if value else ""


def _ppn(person: dict[str, Any]) -> str:
    return f"$3{person.get('idref_ppn')}" if person.get("strong_idref") and person.get("idref_ppn") else ""


def _name_subfields(name: Any) -> str:
    parts = _text(name).split()
    if len(parts) >= 2:
        return f"$a{' '.join(parts[1:])}$b{parts[0]}"
    return f"$a{parts[0] if parts else '[Nom manquant]'}"


def _role_codes(person: dict[str, Any], profile: str) -> str:
    advisor_code = "003" if profile == "memoire_original_imprime" else "727"
    mapping = {
        "author": "070",
        "advisor": advisor_code,
        "jury_president": "956",
        "reviewers": "958",
        "committee_members": "555",
    }
    codes = []
    for role in person.get("roles") or []:
        if role in mapping and mapping[role] not in codes:
            codes.append(mapping[role])
    return "".join(f"$4{code}" for code in codes)


VLM_PROMPT = """You are extracting metadata from a French graduate dissertation title page.
General rules:
- Return ONLY valid JSON.
- Do not add markdown.
- Do not add explanations.
- Do not invent missing data.
- Multivalued fields are always JSON arrays; use [] when empty. Scalar fields use null when absent.
- If source text is written in all-caps, transform it into sentence case with French accents.
- For person names, apply sentence case (e.g. Jean-Daniel DUBOIS → Jean-Daniel Dubois, MARIE-FRANCE Dupont → Marie-France Dupont) and remove all civility and title prefixes (e.g. Monsieur, Madame, M., Mme, Professeur, Pr, Dr, etc.) before returning the name.
Role attribution rules:
- A role is explicit only when a textual role label is directly associated with the person.
- Assign a person to a specific role ONLY if that role is explicitly written on the title page.
- Do NOT infer roles from position, ordering, typography, academic conventions, or prior knowledge.
- Do not infer role equivalence from document structure or visual grouping.
- If people are listed without explicit role labels, place them ONLY in committee_members.
- If no jury or committee members are explicitly listed, use [].
- Do NOT guess who is advisor, reviewer, or jury president.
{
  "title": "Main title as it appears on the title page",
  "subtitle": "Subtitle or remainder of the title, usually following a colon.",
  "author": "Full name of the author (student) who wrote the graduate dissertation",
  "degree_type": "Academic degree sought by the author",
  "discipline": "Academic field or discipline in French",
  "volume": "Volume number as Arabic numeral string, or null",
  "granting_institution": "Institution where the graduate dissertation was submitted",
  "co_tutelle_institutions": [],
  "doctoral_school": "Doctoral school or graduate program, if explicitly mentioned",
  "defense_year": "yyyy",
  "advisor": [],
  "jury_president": null,
  "reviewers": [],
  "committee_members": [],
  "language": "ISO 639-3 code",
  "confidence": 0.0
}"""


WORKFLOW_GRAPH = {
    "schema_version": "2",
    "name": "Humathèque Pipeline",
    "runtime": {"default": "client"},
    "references": [
        {
            "label": "Image",
            "inputs": [{"id": "in", "label": "Image", "type": "image"}],
            "outputs": [{"id": "out", "label": "Image", "type": "image"}],
            "width": 220,
            "asset_type": "image",
            "role": "reference",
            "id": "input-image",
            "data": {},
        },
        {
            "label": "VLM Prompt",
            "inputs": [{"id": "in", "label": "Prompt", "type": "text"}],
            "outputs": [{"id": "out", "label": "Prompt", "type": "text"}],
            "width": 260,
            "asset_type": "text",
            "role": "reference",
            "id": "vlm-prompt",
            "data": {"in": "", "out": VLM_PROMPT},
        },
    ],
    "operators": [
        {
            "label": "HF Gemma Extraction",
            "inputs": [
                {"id": "in_0", "label": "Image", "type": "image", "required": True},
                {"id": "in_1", "label": "Prompt", "type": "text", "required": True},
            ],
            "outputs": [{"id": "out_0", "label": "Metadata JSON", "type": "markdown"}],
            "width": 280,
            "source": "fn",
            "fn": "hf_vlm_extraction",
            "kind": "fn",
            "role": "operator",
            "id": "vlm-extraction",
            "data": {},
        },
        {
            "label": "Alma Verification",
            "inputs": [{"id": "metadata", "label": "Metadata JSON", "type": "text", "required": True}],
            "outputs": [{"id": "out_0", "label": "Alma Result", "type": "text"}],
            "width": 260,
            "source": "fn",
            "fn": "alma_verification",
            "kind": "fn",
            "role": "operator",
            "id": "alma-verification",
            "data": {},
        },
        {
            "label": "Sudoc Verification",
            "inputs": [
                {"id": "alma_result", "label": "Alma Result", "type": "text", "required": True},
            ],
            "outputs": [{"id": "out_0", "label": "Sudoc Result", "type": "text"}],
            "width": 260,
            "source": "fn",
            "fn": "sudoc_verification",
            "kind": "fn",
            "role": "operator",
            "id": "sudoc-verification",
            "data": {},
        },
        {
            "label": "IdRef Persons Alignment",
            "inputs": [
                {"id": "sudoc_result", "label": "Sudoc Result", "type": "text", "required": True},
            ],
            "outputs": [{"id": "out_0", "label": "IdRef Result", "type": "text"}],
            "width": 260,
            "source": "fn",
            "fn": "idref_alignment",
            "kind": "fn",
            "role": "operator",
            "id": "idref-alignment",
            "data": {},
        },
        {
            "label": "IdRef Organizations Alignment",
            "inputs": [
                {"id": "sudoc_result", "label": "Sudoc Result", "type": "text", "required": True},
            ],
            "outputs": [{"id": "out_0", "label": "Organization Result", "type": "text"}],
            "width": 260,
            "source": "fn",
            "fn": "organization_alignment",
            "kind": "fn",
            "role": "operator",
            "id": "organization-alignment",
            "data": {},
        },
        {
            "label": "Dewey Classification",
            "inputs": [
                {"id": "sudoc_result", "label": "Sudoc Result", "type": "text", "required": True},
            ],
            "outputs": [{"id": "out_0", "label": "Dewey Result", "type": "text"}],
            "width": 260,
            "source": "fn",
            "fn": "dewey_classification",
            "kind": "fn",
            "role": "operator",
            "id": "dewey-classification",
            "data": {},
        },
        {
            "label": "UNIMARC Draft",
            "inputs": [
                {"id": "idref_result", "label": "IdRef Result", "type": "text"},
                {"id": "organization_result", "label": "Organization Result", "type": "text"},
                {"id": "dewey_result", "label": "Dewey Result", "type": "text"},
            ],
            "outputs": [{"id": "out_0", "label": "Draft + Pseudo-UNIMARC", "type": "markdown"}],
            "width": 300,
            "source": "fn",
            "fn": "unimarc_draft",
            "kind": "fn",
            "role": "operator",
            "id": "unimarc-draft",
            "data": {},
        },
    ],
    "subjects": [
        {
            "label": "VLM Metadata",
            "inputs": [{"id": "in", "label": "Metadata", "type": "markdown"}],
            "outputs": [{"id": "out", "label": "Metadata", "type": "markdown"}],
            "width": 240,
            "asset_type": "markdown",
            "role": "subject",
            "id": "vlm-metadata-output",
            "data": {},
        },
        {
            "label": "UNIMARC Output",
            "inputs": [{"id": "in", "label": "Draft", "type": "markdown"}],
            "outputs": [{"id": "out", "label": "Draft", "type": "markdown"}],
            "width": 260,
            "asset_type": "markdown",
            "role": "subject",
            "id": "unimarc-output",
            "data": {},
        },
    ],
    "edges": [
        {"id": "image-to-vlm", "from_node_id": "input-image", "from_port_id": "out", "to_node_id": "vlm-extraction", "to_port_id": "in_0", "type": "image"},
        {"id": "prompt-to-vlm", "from_node_id": "vlm-prompt", "from_port_id": "out", "to_node_id": "vlm-extraction", "to_port_id": "in_1", "type": "text"},
        {"id": "vlm-to-metadata-output", "from_node_id": "vlm-extraction", "from_port_id": "out_0", "to_node_id": "vlm-metadata-output", "to_port_id": "in", "type": "markdown"},
        {"id": "metadata-to-alma", "from_node_id": "vlm-metadata-output", "from_port_id": "out", "to_node_id": "alma-verification", "to_port_id": "metadata", "type": "markdown"},
        {"id": "alma-to-sudoc", "from_node_id": "alma-verification", "from_port_id": "out_0", "to_node_id": "sudoc-verification", "to_port_id": "alma_result", "type": "text"},
        {"id": "sudoc-to-idref", "from_node_id": "sudoc-verification", "from_port_id": "out_0", "to_node_id": "idref-alignment", "to_port_id": "sudoc_result", "type": "text"},
        {"id": "sudoc-to-organizations", "from_node_id": "sudoc-verification", "from_port_id": "out_0", "to_node_id": "organization-alignment", "to_port_id": "sudoc_result", "type": "text"},
        {"id": "sudoc-to-dewey", "from_node_id": "sudoc-verification", "from_port_id": "out_0", "to_node_id": "dewey-classification", "to_port_id": "sudoc_result", "type": "text"},
        {"id": "idref-to-unimarc", "from_node_id": "idref-alignment", "from_port_id": "out_0", "to_node_id": "unimarc-draft", "to_port_id": "idref_result", "type": "text"},
        {"id": "organizations-to-unimarc", "from_node_id": "organization-alignment", "from_port_id": "out_0", "to_node_id": "unimarc-draft", "to_port_id": "organization_result", "type": "text"},
        {"id": "dewey-to-unimarc", "from_node_id": "dewey-classification", "from_port_id": "out_0", "to_node_id": "unimarc-draft", "to_port_id": "dewey_result", "type": "text"},
        {"id": "unimarc-to-output", "from_node_id": "unimarc-draft", "from_port_id": "out_0", "to_node_id": "unimarc-output", "to_port_id": "in", "type": "markdown"},
    ],
    "view": {"default": "canvas"},
}


def _embedded_graph_path() -> str:
    graph_file = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
    with graph_file:
        json.dump(WORKFLOW_GRAPH, graph_file, ensure_ascii=False, indent=2)
    return graph_file.name


gr.Workflow(
    graph=_embedded_graph_path(),
    bind={
        "hf_vlm_extraction": hf_vlm_extraction,
        "alma_verification": alma_verification,
        "sudoc_verification": sudoc_verification,
        "idref_alignment": idref_alignment,
        "organization_alignment": organization_alignment,
        "dewey_classification": dewey_classification,
        "unimarc_draft": unimarc_draft,
    },
).launch()