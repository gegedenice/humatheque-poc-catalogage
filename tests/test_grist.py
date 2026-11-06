"""Lecture Grist simulée (respx) : résolution des références par le lecteur générique,
chargement de Statuts_services depuis Grist et repli sur le CSV (SPEC §8.1)."""

import httpx
import pytest
import respx

from humatheque.config import Reglages
from humatheque.regles import charger_regles, verifier
from humatheque.regles.grist import lire_tables_grist

URL = "http://grist.test"
DOC = "doc123"
BASE = f"{URL}/api/docs/{DOC}"


def _col(col_id, label, type_="Text"):
    return {"id": col_id, "fields": {"label": label, "type": type_}}


def _rec(row_id, **fields):
    return {"id": row_id, "fields": fields}


TABLES = {
    "Profils": (
        [_col("code", "code"), _col("type_document", "type_document"), _col("statut", "statut")],
        [_rec(1, code="these_originale_imprimee", type_document="these", statut="seed"),
         _rec(2, code="these_reproduction_imprimee", type_document="these", statut="seed")],
    ),
    # Grist réserve la colonne `id` : la colonne s'appelle id2 avec le libellé `id`.
    "Regles": (
        [_col("id2", "id"), _col("zone", "zone"), _col("profils", "profils", "RefList:Profils"),
         _col("priorite", "priorite", "Numeric"), _col("mode_production", "mode_production"),
         _col("sources", "sources", "ChoiceList"), _col("gabarit", "gabarit"),
         _col("repli", "repli"), _col("statut", "statut")],
        [_rec(1, id2="R005", zone="101$a", profils=["L", 1, 2], priorite=1.0,
              mode_production="Extraction", sources=["L", "language"], gabarit="101 0#$a{language}",
              repli=None, statut="seed"),
         _rec(2, id2="R010", zone="105$b", profils=["L", 1], priorite=1,
              mode_production="Règle métier", sources=None, gabarit="$bm$ba", repli="", statut="seed")],
    ),
    "Sources": (
        [_col("id2", "id"), _col("type_source", "type_source"), _col("cardinalit_", "cardinalité")],
        [_rec(1, id2="language", type_source="champ extrait", cardinalit_="1")],
    ),
    "Situations": (
        [_col("id2", "id"), _col("etat_alma", "etat_alma"), _col("etat_sudoc", "etat_sudoc"),
         _col("provenance", "provenance"), _col("action", "action"),
         _col("profil_cible", "profil_cible", "Ref:Profils"), _col("routage", "routage")],
        [_rec(1, id2="S08", etat_alma="absent", etat_sudoc="rien", provenance="EPHE",
              action="Créer", profil_cible=1, routage="auto"),
         _rec(2, id2="S10", etat_alma="absent", etat_sudoc="ambigu", provenance="*",
              action="Arrêt", profil_cible=0, routage="décision")],
    ),
    "Statuts_services": (
        [_col("service", "service"), _col("statut", "statut"), _col("routage", "routage")],
        [_rec(1, service="sudoc-checker", statut="error", routage="incident")],
    ),
}


def _reglages():
    return Reglages(_env_file=None, spec_source="grist", grist_url=URL, grist_doc_id=DOC,
                    grist_api_key="cle-de-test", http_timeout=2)


def _simuler_grist(mock):
    for t, (cols, recs) in TABLES.items():
        mock.get(f"{BASE}/tables/{t}/columns").respond(json={"columns": cols})
        mock.get(f"{BASE}/tables/{t}/records").respond(json={"records": recs})


@respx.mock
def test_lecteur_grist_resout_references_et_listes():
    _simuler_grist(respx)
    t = lire_tables_grist(URL, DOC, "cle", ["Regles", "Situations"], timeout=2)
    r005, r010 = t["Regles"]
    assert r005["id"] == "R005" and r005["profils"] == "these_originale_imprimee, these_reproduction_imprimee"
    assert r005["sources"] == "language" and r005["priorite"] == 1.0
    assert r010["profils"] == "these_originale_imprimee"
    assert t["Situations"][0]["profil_cible"] == "these_originale_imprimee"
    assert t["Situations"][1]["profil_cible"] == ""


@respx.mock
def test_statuts_services_depuis_grist():
    _simuler_grist(respx)
    r = charger_regles(_reglages())
    assert r.source == "grist" and not r.avertissements
    assert [s.id for s in r.statuts_services] == ["sudoc-checker / error"]
    assert r.profils  # les règles viennent toujours de regles/


@respx.mock
def test_chargement_grist_envoie_la_cle_api():
    _simuler_grist(respx)
    charger_regles(_reglages())
    assert all(c.request.headers["Authorization"] == "Bearer cle-de-test" for c in respx.calls)


@respx.mock
@pytest.mark.parametrize("panne", [
    httpx.ConnectError("connexion refusée"),
    httpx.ReadTimeout("délai dépassé"),
    httpx.Response(500),
    httpx.Response(404, json={"error": "table not found"}),
])
def test_grist_injoignable_repli_sur_csv(panne):
    route = respx.get(url__startswith=URL)
    if isinstance(panne, Exception):
        route.side_effect = panne
    else:
        route.return_value = panne
    r = charger_regles(_reglages())
    assert r.source == "csv"
    assert len(r.avertissements) == 1 and "Grist injoignable" in r.avertissements[0]
    assert len(r.statuts_services) > 20
    rapport = verifier(r)
    assert not rapport.bloquant
    assert any(c.code == "chargement" for c in rapport.avertissements)


def test_grist_sans_identifiant_de_document_repli_sur_csv():
    r = charger_regles(Reglages(_env_file=None, spec_source="grist", grist_doc_id=""))
    assert r.source == "csv" and "GRIST_DOC_ID" in r.avertissements[0]
