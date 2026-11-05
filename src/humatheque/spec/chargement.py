"""Chargement de la spécification depuis les CSV (`spec/*.csv`) ou Grist (SPEC §8).

En mode `grist`, si Grist est injoignable, on se replie sur les CSV et un
avertissement est attaché à la spec (affiché dans l'interface et par
`check-spec`).
"""

from __future__ import annotations

import csv
import hashlib
import json
import unicodedata
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import httpx

from humatheque.config import Reglages, reglages as reglages_par_defaut
from humatheque.spec.grist import GristInjoignable, lire_tables_grist
from humatheque.spec.modeles import Profil, Regle, Situation, Source, Spec, StatutService

# Table Grist → (modèle, attribut de Spec)
TABLES: dict[str, tuple[type, str]] = {
    "Regles": (Regle, "regles"),
    "Sources": (Source, "sources"),
    "Situations": (Situation, "situations"),
    "Statuts_services": (StatutService, "statuts_services"),
    "Profils": (Profil, "profils"),
}


class SpecIntrouvable(Exception):
    """Une ou plusieurs tables attendues manquent."""


def normaliser_entete(nom: str) -> str:
    """`cardinalité` → `cardinalite` : en-têtes sans accent, minuscules."""
    sans_accent = unicodedata.normalize("NFKD", nom.strip())
    return "".join(c for c in sans_accent if not unicodedata.combining(c)).lower()


def lire_tables_csv(dossier: Path) -> dict[str, list[dict[str, Any]]]:
    manquantes = [t for t in TABLES if not (dossier / f"{t}.csv").exists()]
    if manquantes:
        raise SpecIntrouvable(
            f"Tables de spécification manquantes dans {dossier} : "
            + ", ".join(f"{t}.csv" for t in manquantes)
            + " (à exporter depuis Grist)"
        )
    tables = {}
    for t in TABLES:
        with (dossier / f"{t}.csv").open(encoding="utf-8-sig", newline="") as f:
            tables[t] = list(csv.DictReader(f))
    return tables


def _empreinte(spec_tables: dict[str, list[Any]]) -> str:
    contenu = {t: [ligne.model_dump() for ligne in lignes] for t, lignes in spec_tables.items()}
    texte = json.dumps(contenu, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(texte.encode("utf-8")).hexdigest()[:12]


def construire_spec(
    tables: dict[str, list[dict[str, Any]]],
    source: Literal["csv", "grist"],
    emplacement: str,
    avertissements: list[str] | None = None,
) -> Spec:
    manquantes = [t for t in TABLES if t not in tables]
    if manquantes:
        raise SpecIntrouvable(f"Tables de spécification manquantes : {', '.join(manquantes)}")
    modeles: dict[str, list[Any]] = {}
    for t, (modele, attribut) in TABLES.items():
        lignes = []
        for i, brute in enumerate(tables[t], start=2):
            ligne = {normaliser_entete(k): v for k, v in brute.items() if k is not None}
            if not any(str(v or "").strip() for v in ligne.values()):
                continue  # ligne vide
            try:
                lignes.append(modele.model_validate(ligne))
            except ValueError as e:
                raise SpecIntrouvable(f"{t}, ligne {i} illisible : {e}") from e
        modeles[attribut] = lignes
    return Spec(
        **modeles,
        source=source,
        emplacement=emplacement,
        horodatage=datetime.now(UTC),
        empreinte=_empreinte(modeles),
        avertissements=avertissements or [],
    )


def charger_spec(
    reglages: Reglages | None = None,
    source: Literal["csv", "grist"] | None = None,
    dossier: Path | None = None,
    client: httpx.Client | None = None,
) -> Spec:
    """Charge la spec selon `SPEC_SOURCE` (ou `source`), avec repli CSV si Grist échoue."""
    r = reglages or reglages_par_defaut()
    source = source or r.spec_source
    dossier = dossier or r.dossier_spec

    if source == "grist":
        try:
            tables = lire_tables_grist(
                r.grist_url,
                r.grist_doc_id,
                r.grist_api_key.get_secret_value(),
                list(TABLES),
                timeout=r.http_timeout,
                client=client,
            )
            return construire_spec(tables, "grist", f"{r.grist_url}/doc/{r.grist_doc_id}")
        except GristInjoignable as e:
            alerte = f"Grist injoignable ({e}) : repli sur les CSV de {dossier}"
            return construire_spec(lire_tables_csv(dossier), "csv", str(dossier), [alerte])

    return construire_spec(lire_tables_csv(dossier), "csv", str(dossier))
