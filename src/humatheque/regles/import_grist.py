"""Création de la table Grist Statuts_services depuis `spec/` (`humatheque spec-import`).

Pendant de `spec-export` : chaque CSV devient une table Grist dont les
colonnes sont toutes de type Texte, pour garder les valeurs telles quelles
(`008` reste `008`, `1` reste `1`). L'identifiant d'une colonne Grist doit être
un identifiant ASCII et `id` est réservé : on dérive donc un identifiant sûr,
et le **libellé** garde l'en-tête exact du CSV, qui est ce que lit le chargeur
(`spec/grist.py`). Après l'import, les tables sont relues par le chargeur et
comparées aux CSV : on obtient la même spécification dans les deux sources.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

import httpx
from unidecode import unidecode

from humatheque.config import Reglages
from humatheque.regles.chargement import TABLE_STATUTS, lire_statuts_csv
from humatheque.regles.grist import GristInjoignable, lire_tables_grist
from humatheque.regles.modeles import StatutService

TABLES = [TABLE_STATUTS]

# Identifiants de colonne réservés par Grist.
RESERVES = {"id", "manualsort"}
LOT_ENREGISTREMENTS = 200



def _tables_csv(dossier: Path) -> dict[str, list[dict[str, Any]]]:
    return {TABLE_STATUTS: lire_statuts_csv(dossier)}


def _empreinte_table(tables: dict[str, list[dict[str, Any]]]) -> str:
    """Empreinte de Statuts_services (lignes validées) : identique entre CSV et Grist."""
    lignes = [StatutService.model_validate(l).model_dump() for l in tables.get(TABLE_STATUTS, [])
              if any(str(v or "").strip() for v in l.values())]
    return hashlib.sha256(json.dumps(lignes, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:12]

class ImportImpossible(Exception):
    """Classeur cible inutilisable (tables déjà présentes, document introuvable…)."""


def identifiant_colonne(entete: str, pris: set[str]) -> str:
    """En-tête CSV → identifiant de colonne Grist (ASCII, unique, non réservé)."""
    base = re.sub(r"\W+", "_", unidecode(entete)).strip("_") or "colonne"
    if base[0].isdigit():
        base = "c_" + base
    if base.lower() in RESERVES:
        base = f"{base}_spec"
    ident, n = base, 2
    while ident.lower() in pris:
        ident, n = f"{base}_{n}", n + 1
    pris.add(ident.lower())
    return ident


def _requete(client: httpx.Client, methode: str, url: str, json: Any = None) -> Any:
    try:
        r = client.request(methode, url, json=json)
        r.raise_for_status()
        return r.json() if r.content else None
    except httpx.HTTPStatusError as e:
        raise GristInjoignable(f"{methode} {url} : HTTP {e.response.status_code} {e.response.text[:300]}") from e
    except (httpx.HTTPError, ValueError) as e:
        raise GristInjoignable(f"{methode} {url} : {e}") from e


def _creer_document(client: httpx.Client, racine: str, nom: str, org: str, espace: int | None) -> str:
    if espace is None:
        espaces = _requete(client, "GET", f"{racine}/api/orgs/{org}/workspaces")
        if not espaces:
            raise ImportImpossible(f"aucun espace de travail dans l'organisation « {org} »")
        espace = espaces[0]["id"]
    return _requete(client, "POST", f"{racine}/api/workspaces/{espace}/docs", {"name": nom})


def importer(reglages: Reglages, dossier: Path | None = None, *, doc_id: str | None = None,
             nouveau: str | None = None, org: str = "current", espace: int | None = None,
             remplacer: bool = False, client: httpx.Client | None = None) -> dict[str, Any]:
    """Crée (ou complète) un classeur Grist avec les tables de `spec/`.

    `nouveau` : nom d'un document à créer ; sinon `doc_id` (défaut GRIST_DOC_ID).
    `remplacer` : supprime d'abord les tables de spec déjà présentes.
    Renvoie {doc_id, tables: {table: nb lignes}, ecarts: [..], empreinte}.
    """
    dossier = dossier or reglages.dossier_spec
    csv_tables = _tables_csv(dossier)
    empreinte_csv = _empreinte_table(csv_tables)  # la table source doit être lisible
    racine = reglages.grist_url.rstrip("/")
    propre = client is None
    client = client or httpx.Client(headers={"Authorization": f"Bearer {reglages.grist_api_key.get_secret_value()}"},
                                    timeout=reglages.http_timeout)
    try:
        if nouveau:
            doc_id = _creer_document(client, racine, nouveau, org, espace)
        doc_id = doc_id or reglages.grist_doc_id
        if not doc_id:
            raise ImportImpossible("aucun document : indiquer --nouveau NOM, --doc ID ou GRIST_DOC_ID")
        base = f"{racine}/api/docs/{doc_id}"
        existantes = {t["id"] for t in (_requete(client, "GET", f"{base}/tables") or {}).get("tables", [])}

        a_retirer = [t for t in TABLES if t in existantes]
        if a_retirer and not remplacer:
            raise ImportImpossible(f"tables déjà présentes dans le document {doc_id} : {', '.join(a_retirer)} "
                                   "(--remplacer pour les recréer)")
        # Document neuf : Grist crée une table vide « Table1 », retirée si elle est vide.
        if nouveau and "Table1" in existantes:
            if not (_requete(client, "GET", f"{base}/tables/Table1/records") or {}).get("records"):
                a_retirer.append("Table1")
        if a_retirer:
            _requete(client, "POST", f"{base}/apply", [["RemoveTable", t] for t in a_retirer])

        definitions, correspondances = [], {}
        for table in TABLES:
            lignes = csv_tables.get(table, [])
            entetes = list(lignes[0]) if lignes else []
            pris: set[str] = set()
            corr = {e: identifiant_colonne(e, pris) for e in entetes}
            correspondances[table] = corr
            definitions.append({"id": table, "columns": [
                {"id": ident, "fields": {"label": entete, "type": "Text"}} for entete, ident in corr.items()]})
        _requete(client, "POST", f"{base}/tables", {"tables": definitions})

        for table in TABLES:
            corr = correspondances[table]
            enregistrements = [{"fields": {corr[k]: (v or "") for k, v in ligne.items() if k in corr}}
                               for ligne in csv_tables.get(table, [])]
            for i in range(0, len(enregistrements), LOT_ENREGISTREMENTS):
                _requete(client, "POST", f"{base}/tables/{table}/records",
                         {"records": enregistrements[i:i + LOT_ENREGISTREMENTS]})

        # Contrôle : relecture par le chargeur et comparaison aux CSV.
        relu = lire_tables_grist(racine, doc_id, "", list(TABLES), timeout=reglages.http_timeout, client=client)
    finally:
        if propre:
            client.close()

    ecarts = comparer(csv_tables, relu)
    empreinte_grist = _empreinte_table(relu)
    return {"doc_id": doc_id, "tables": {t: len(csv_tables.get(t, [])) for t in TABLES}, "ecarts": ecarts,
            "empreinte_csv": empreinte_csv, "empreinte_grist": empreinte_grist}


def comparer(a: dict[str, list[dict[str, Any]]], b: dict[str, list[dict[str, Any]]]) -> list[str]:
    """Différences ligne à ligne entre deux jeux de tables (valeurs comparées en texte)."""
    norm = lambda v: "" if v is None else str(v)  # noqa: E731
    ecarts = []
    for table in TABLES:
        la, lb = a.get(table, []), b.get(table, [])
        if len(la) != len(lb):
            ecarts.append(f"{table} : {len(la)} ligne(s) en CSV, {len(lb)} dans Grist")
            continue
        for i, (x, y) in enumerate(zip(la, lb), start=1):
            for k in x:
                if norm(x[k]) != norm(y.get(k)):
                    ecarts.append(f"{table} ligne {i}, colonne « {k} » : {x[k]!r} ≠ {y.get(k)!r}")
    return ecarts
