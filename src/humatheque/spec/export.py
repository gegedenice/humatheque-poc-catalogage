"""Instantané des tables Grist dans `spec/` (`humatheque spec-export`, SPEC §8).

Même format que les exports manuels : un CSV par table, en-têtes = libellés
des colonnes Grist, références résolues en codes, listes jointes par des
virgules. Une copie horodatée est écrite dans `spec/archives/<horodatage>/`.
"""

from __future__ import annotations

import csv
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from humatheque.config import Reglages
from humatheque.spec.chargement import TABLES, construire_spec
from humatheque.spec.grist import lire_tables_grist


def _cellule(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def _ecrire(chemin: Path, lignes: list[dict[str, Any]]) -> None:
    chemin.parent.mkdir(parents=True, exist_ok=True)
    colonnes = list(lignes[0]) if lignes else []
    with chemin.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=colonnes, lineterminator="\n")
        w.writeheader()
        for ligne in lignes:
            w.writerow({k: _cellule(v) for k, v in ligne.items()})


def exporter(reglages: Reglages, dossier: Path | None = None, client: httpx.Client | None = None) -> dict[str, Any]:
    """Lit Grist (erreur si injoignable : pas de repli ici) et écrit l'instantané.

    Renvoie {horodatage, empreinte, archive, tables: {table: nb lignes}}.
    """
    dossier = dossier or reglages.dossier_spec
    tables = lire_tables_grist(reglages.grist_url, reglages.grist_doc_id, reglages.grist_api_key.get_secret_value(),
                               list(TABLES), timeout=reglages.http_timeout, client=client)
    spec = construire_spec(tables, "grist", reglages.grist_url)  # contrôle de lisibilité avant écriture
    horodatage = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    archive = dossier / "archives" / horodatage
    for table, lignes in tables.items():
        _ecrire(dossier / f"{table}.csv", lignes)
        _ecrire(archive / f"{table}.csv", lignes)
    return {"horodatage": horodatage, "empreinte": spec.empreinte, "archive": str(archive),
            "tables": {t: len(l) for t, l in tables.items()}}
