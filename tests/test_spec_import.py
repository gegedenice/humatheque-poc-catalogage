"""humatheque spec-import : table Grist Statuts_services créée depuis spec/ (SPEC §8.1).

Un faux Grist en mémoire reproduit les routes REST utilisées (espaces, documents,
tables, colonnes, enregistrements, actions) ; l'aller-retour doit redonner la
même spécification que les CSV.
"""

import json
import re

import httpx
import pytest
import respx

from humatheque.config import Reglages
from humatheque.regles import charger_regles
from humatheque.regles.chargement import lire_statuts_csv
from humatheque.regles.import_grist import ImportImpossible, identifiant_colonne, importer

URL = "http://grist.test"


class FauxGrist:
    def __init__(self):
        self.docs: dict[str, dict[str, dict]] = {}  # doc → table → {columns, records}

    def nouveau_doc(self, doc="docNeuf"):
        self.docs[doc] = {"Table1": {"columns": [{"id": "A", "fields": {"label": "A", "type": "Any"}}], "records": []}}
        return doc

    def __call__(self, request: httpx.Request) -> httpx.Response:
        chemin, m = request.url.path, request.method
        corps = json.loads(request.content) if request.content else None
        if m == "GET" and re.fullmatch(r"/api/orgs/[^/]+/workspaces", chemin):
            return httpx.Response(200, json=[{"id": 7, "name": "Home"}])
        if m == "POST" and chemin == "/api/workspaces/7/docs":
            return httpx.Response(200, json=self.nouveau_doc())
        r = re.fullmatch(r"/api/docs/([^/]+)(/.*)", chemin)
        if not r or r.group(1) not in self.docs:
            return httpx.Response(404, json={"error": "document introuvable"})
        doc, reste = self.docs[r.group(1)], r.group(2)
        if reste == "/tables" and m == "GET":
            return httpx.Response(200, json={"tables": [{"id": t} for t in doc]})
        if reste == "/tables" and m == "POST":
            for t in corps["tables"]:
                assert t["id"] not in doc
                doc[t["id"]] = {"columns": t["columns"], "records": []}
            return httpx.Response(200, json={"tables": [{"id": t["id"]} for t in corps["tables"]]})
        if reste == "/apply" and m == "POST":
            for action, table in corps:
                assert action == "RemoveTable"
                del doc[table]
            return httpx.Response(200, json={})
        r = re.fullmatch(r"/tables/([^/]+)/(columns|records)", reste)
        if r:
            table = doc[r.group(1)]
            if r.group(2) == "columns":
                return httpx.Response(200, json={"columns": table["columns"]})
            if m == "POST":
                ids = {c["id"] for c in table["columns"]}
                for e in corps["records"]:
                    assert set(e["fields"]) <= ids, "colonne inconnue"
                    table["records"].append({"id": len(table["records"]) + 1, "fields": e["fields"]})
                return httpx.Response(200, json={"records": [{"id": e["id"]} for e in table["records"]]})
            return httpx.Response(200, json={"records": table["records"]})
        return httpx.Response(404)


def _reglages(doc=""):
    return Reglages(_env_file=None, spec_source="grist", grist_url=URL, grist_doc_id=doc,
                    grist_api_key="cle-de-test", http_timeout=2)


@pytest.fixture
def grist():
    faux = FauxGrist()
    respx.route(url__startswith=URL).mock(side_effect=faux)
    return faux


@respx.mock
def test_import_nouveau_document_aller_retour(grist):
    r = importer(_reglages(), nouveau="Statuts des services humatheque")
    assert r["doc_id"] == "docNeuf" and r["ecarts"] == []
    assert set(grist.docs["docNeuf"]) == {"Statuts_services"}  # Table1 vide retirée
    assert r["tables"] == {"Statuts_services": len(lire_statuts_csv(_reglages().dossier_spec))}
    cols = {c["fields"]["label"]: c for c in grist.docs["docNeuf"]["Statuts_services"]["columns"]}
    assert {c["fields"]["type"] for c in cols.values()} == {"Text"}
    # même table depuis Grist que depuis le CSV
    assert r["empreinte_grist"] == r["empreinte_csv"]
    depuis_grist = charger_regles(_reglages("docNeuf"))
    assert depuis_grist.source == "grist" and depuis_grist.empreinte == charger_regles().empreinte


@respx.mock
def test_import_document_existant_refuse_puis_remplace(grist):
    importer(_reglages(), nouveau="statuts")
    with pytest.raises(ImportImpossible, match="déjà présentes"):
        importer(_reglages("docNeuf"))
    r = importer(_reglages("docNeuf"), remplacer=True)
    assert r["ecarts"] == [] and len(grist.docs["docNeuf"]["Statuts_services"]["records"]) == r["tables"]["Statuts_services"]


@respx.mock
def test_import_sans_document(grist):
    with pytest.raises(ImportImpossible, match="aucun document"):
        importer(_reglages())


def test_identifiant_colonne():
    pris: set[str] = set()
    assert [identifiant_colonne(e, pris) for e in ["id", "cardinalité", "Zone 2", "zone_2", "1er"]] == \
        ["id_spec", "cardinalite", "Zone_2", "zone_2_2", "c_1er"]
