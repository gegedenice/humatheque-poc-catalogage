"""Lecture des tables de spécification par l'API REST Grist (SPEC §8).

Les colonnes sont renommées d'après leur libellé (celui des exports CSV), les
références (`Ref:T`, entier) et listes de références (`RefList:T`,
`["L", id…]`) sont résolues en codes, les listes de choix (`["L", "a", …]`)
jointes par des virgules : on obtient les mêmes lignes qu'un export CSV.
"""

from __future__ import annotations

from typing import Any

import httpx


class GristInjoignable(Exception):
    """Grist ne répond pas ou renvoie une erreur : l'appelant se replie sur les CSV."""


# Colonnes candidates, dans l'ordre, pour représenter une ligne référencée.
COLONNES_CLE = ("code", "id")


def _get(client: httpx.Client, url: str) -> Any:
    try:
        r = client.get(url)
        r.raise_for_status()
        return r.json()
    except (httpx.HTTPError, ValueError) as e:
        raise GristInjoignable(f"{url} : {e}") from e


def _colonnes(client: httpx.Client, base: str, table: str) -> list[dict[str, Any]]:
    return _get(client, f"{base}/tables/{table}/columns").get("columns", [])


def _enregistrements(client: httpx.Client, base: str, table: str) -> list[dict[str, Any]]:
    return _get(client, f"{base}/tables/{table}/records").get("records", [])


def _libelle(col: dict[str, Any]) -> str:
    return (col.get("fields") or {}).get("label") or col["id"]


def _cible(type_col: str) -> tuple[str, str] | None:
    """`Ref:Profils` → ("Ref", "Profils") ; None pour un autre type."""
    for genre in ("RefList", "Ref"):
        if type_col.startswith(genre + ":"):
            return genre, type_col.split(":", 1)[1]
    return None


def lire_tables_grist(
    url: str,
    doc_id: str,
    cle_api: str,
    tables: list[str],
    timeout: float,
    client: httpx.Client | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Renvoie `{table: [ligne {libellé: valeur}]}` ; lève `GristInjoignable`."""
    if not doc_id:
        raise GristInjoignable("GRIST_DOC_ID non renseigné")
    base = f"{url.rstrip('/')}/api/docs/{doc_id}"
    propre = client is None
    client = client or httpx.Client(
        headers={"Authorization": f"Bearer {cle_api}"}, timeout=timeout
    )
    try:
        brut: dict[str, tuple[list[dict], list[dict]]] = {}
        a_lire = list(tables)
        # Les tables cibles de références sont lues aussi (pour résoudre les codes).
        while a_lire:
            t = a_lire.pop(0)
            if t in brut:
                continue
            cols = _colonnes(client, base, t)
            brut[t] = (cols, _enregistrements(client, base, t))
            for c in cols:
                cible = _cible((c.get("fields") or {}).get("type", ""))
                if cible and cible[1] not in brut:
                    a_lire.append(cible[1])
    finally:
        if propre:
            client.close()

    # Code de chaque ligne de chaque table : {table: {row_id: code}}
    codes: dict[str, dict[int, str]] = {}
    for t, (cols, recs) in brut.items():
        par_libelle = {_libelle(c): c["id"] for c in cols}
        col_cle = next((par_libelle[k] for k in COLONNES_CLE if k in par_libelle), None)
        if col_cle is None and cols:
            col_cle = cols[0]["id"]
        codes[t] = {r["id"]: str(r["fields"].get(col_cle, "")) for r in recs}

    resultat: dict[str, list[dict[str, Any]]] = {}
    for t in tables:
        cols, recs = brut[t]
        lignes = []
        for r in recs:
            ligne: dict[str, Any] = {}
            for c in cols:
                type_col = (c.get("fields") or {}).get("type", "")
                v = r["fields"].get(c["id"])
                cible = _cible(type_col)
                if cible and cible[0] == "Ref":
                    v = codes[cible[1]].get(v, "") if v else ""
                elif cible and cible[0] == "RefList":
                    ids = v[1:] if isinstance(v, list) and v[:1] == ["L"] else []
                    v = ", ".join(codes[cible[1]][i] for i in ids if i in codes[cible[1]])
                elif isinstance(v, list) and v[:1] == ["L"]:
                    v = ", ".join(str(x) for x in v[1:])
                ligne[_libelle(c)] = v
            lignes.append(ligne)
        resultat[t] = lignes
    return resultat
