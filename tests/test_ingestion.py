"""Ingestion S3 et téléversement de dossiers (SPEC §3.1, §11 étape 6 bis), bucket simulé."""

import html
import shutil

import pytest
import respx
from fastapi.testclient import TestClient

import services_simules as sim
from conftest import RACINE
from humatheque.application import ActionImpossible, Application, ExecuteurSynchrone
from humatheque.config import Reglages
from humatheque.ingestion import (
    DepotIndisponible, constituer_documents, documents_du_depot, parametres_connexion,
)
from humatheque.store import Store
from humatheque.web import create_app
from services_simules import EXTRACTION, simuler


class BucketSimule:
    """Bucket S3 en mémoire (même interface que DepotS3)."""

    def __init__(self, objets: dict[str, bytes], bucket="images", prefixe="poc/", panne=False):
        self.objets, self.bucket, self.prefixe, self.panne = objets, bucket, prefixe, panne
        self.lectures: list[str] = []

    def lister(self):
        if self.panne:
            raise DepotIndisponible("connexion refusée")
        return sorted(self.objets)

    def verifier(self):
        if self.panne:
            raise DepotIndisponible("connexion refusée")
        return True

    def lire(self, cle):
        self.lectures.append(cle)
        if cle not in self.objets:
            raise DepotIndisponible(f"{cle} : NoSuchKey")
        return self.objets[cle]


def bucket(**kw):
    page = sim.png()
    return BucketSimule({
        "poc/TH/TH-01/002.png": page, "poc/TH/TH-01/001.jpg": page,   # multi-images, ordre lexicographique
        "poc/TH/TH-02/": b"",                                          # dossier vide (marqueur S3)
        "poc/MM/MM-01/page_titre.jpg": page,
        "poc/MM/MM-01/notes.txt": b"pas une image",
        "poc/TH/": b"",
        "autre/TH/TH-09/001.jpg": page,                                # hors préfixe
    }, **kw)


@pytest.fixture
def app(tmp_path):
    shutil.copytree(RACINE / "spec", tmp_path / "spec")
    shutil.copytree(RACINE / "regles", tmp_path / "regles", ignore=shutil.ignore_patterns("archives"),
                    dirs_exist_ok=True)
    reglages = Reglages(_env_file=None).model_copy(update={"racine": tmp_path, "spec_source": "csv"})
    return Application(store=Store(tmp_path / "data" / "t.db"), reglages=reglages,
                       fabrique=lambda s, f: sim.services(s, f), executeur=ExecuteurSynchrone(), depot=bucket())


# ---------------------------------------------------------------- constitution des documents
def test_documents_du_bucket():  # ING-F-001, ING-F-002, ING-F-009
    docs = {d.id: d for d in documents_du_depot(bucket())}
    assert set(docs) == {"TH/TH-01", "TH/TH-02", "MM/MM-01"}
    assert docs["TH/TH-01"].images == ["s3://images/poc/TH/TH-01/001.jpg", "s3://images/poc/TH/TH-01/002.png"]
    assert (docs["TH/TH-01"].type_document, docs["MM/MM-01"].type_document) == ("these", "memoire")
    assert docs["MM/MM-01"].images == ["s3://images/poc/MM/MM-01/page_titre.jpg"]
    assert (docs["TH/TH-02"].images, docs["TH/TH-02"].invalide) == ([], "dossier sans image")  # ING-F-008


def test_constitution_arborescence_televersee():
    docs = constituer_documents(["export/TH/TH-01/b.png", "export/TH/TH-01/a.png", "these-x/p1.jpg",
                                 "inclassable/x/p.jpg"], type_par_defaut=None)
    par_id = {d.id: d for d in docs}
    assert par_id["TH/TH-01"].images == ["export/TH/TH-01/a.png", "export/TH/TH-01/b.png"]
    assert par_id["these-x"].invalide.startswith("type de document inconnu")
    assert constituer_documents(["these-x/p1.jpg"], type_par_defaut="memoire")[0].type_document == "memoire"


@pytest.mark.parametrize("endpoint, hote, securise", [
    ("https://minio.smartbiblia.fr", "minio.smartbiblia.fr", True),
    ("http://minio:9000", "minio:9000", False),
    ("localhost:9000", "localhost:9000", False),
    ("s3.eu-west-3.amazonaws.com", "s3.eu-west-3.amazonaws.com", True),
])
def test_parametres_connexion(endpoint, hote, securise):
    assert parametres_connexion(endpoint) == (hote, securise)


# ---------------------------------------------------------------- balayage, sélection, historique
@respx.mock
def test_balayage_selection_et_historique(app):  # ING-F-005, ING-F-006, ING-T-003
    v = app.balayer()
    assert v.configure and [l.document.id for l in v.dossiers] == ["MM/MM-01", "TH/TH-01", "TH/TH-02"]
    assert all(l.nouveau for l in v.dossiers)

    routes = simuler()
    lot = app.lancer_selection(["TH/TH-01", "MM/MM-01", "TH/TH-02"])
    lignes = {l.document_id: l for l in app.store.lignes_du_lot(lot.id)}
    d_th = app.vue_dossier(lignes["TH/TH-01"].id).dossier
    assert d_th.document.type_document == "these" and d_th.routage == "auto"
    assert app.depot.lectures[:2] == ["poc/TH/TH-01/001.jpg", "poc/TH/TH-01/002.png"]  # images lues dans le bucket
    d_mm = app.vue_dossier(lignes["MM/MM-01"].id).dossier
    assert d_mm.document.type_document == "memoire" and d_mm.provenance.valeur == "mémoire"
    assert any(c.request.url.path == "/check/dissertation" for c in routes["alma"].calls)
    d_vide = app.vue_dossier(lignes["TH/TH-02"].id).dossier
    assert (d_vide.routage, d_vide.etape_arret) == ("décision", "extraction")
    assert any(a.startswith("document_invalide") for a in d_vide.alertes)

    v = app.balayer()
    statuts = {l.document.id: (l.nouveau, l.routage) for l in v.dossiers}
    assert statuts == {"MM/MM-01": (False, "auto"), "TH/TH-01": (False, "auto"), "TH/TH-02": (False, "décision")}
    types = [(i.type, i.resultat) for i in app.historique().ingestions]
    assert ("balayage", {"documents": 3, "nouveaux": 0}) in types
    assert ("balayage", {"documents": 3, "nouveaux": 3}) in types
    assert any(t == "traitement" for t, _ in types)


def test_selection_inconnue_ou_vide(app):
    with pytest.raises(ActionImpossible, match="absent"):
        app.lancer_selection(["TH/TH-99"])
    with pytest.raises(ActionImpossible):
        app.lancer_selection([])


def test_bucket_injoignable_et_non_configure(app):
    app.depot = bucket(panne=True)
    v = app.balayer()
    assert v.configure and "injoignable" in v.erreur
    assert "erreur" in app.historique().ingestions[0].resultat
    app.depot = None
    assert app.balayer().configure is False
    with pytest.raises(ActionImpossible, match="non configuré"):
        app.lancer_selection(["TH/TH-01"])


@respx.mock
def test_image_s3_inaccessible_incident(app):
    simuler()
    app.depot.objets.pop("poc/TH/TH-01/002.png")
    app.depot.objets["poc/TH/TH-01/002.png"] = b"x"
    lot = app.lancer_selection(["TH/TH-01"])
    del app.depot.objets["poc/TH/TH-01/001.jpg"]
    (ligne,) = app.store.lignes_du_lot(lot.id)
    assert app.vue_dossier(ligne.id).dossier.routage == "décision"  # 002.png illisible → document invalide
    app.relancer(ligne.id, depuis="extraction")
    assert app.vue_dossier(ligne.id).dossier.etapes["extraction"].statut == "image_inaccessible"


@respx.mock
def test_traiter_nouveaux(app):  # architecture ING-F-003 / ING-F-004
    simuler()
    lot = app.traiter_nouveaux("planifie")
    assert lot.declencheur == "planifie" and lot.nb_documents == 3
    assert app.traiter_nouveaux("planifie") is None


@respx.mock
def test_televerser_dossiers(app):
    simuler()
    page = sim.png()
    lot = app.televerser_dossiers([("export/TH/TH-05/p2.png", page), ("export/TH/TH-05/p1.png", page),
                                   ("doc-libre/page.png", page)], type_par_defaut="memoire")
    lignes = {l.document_id: l for l in app.store.lignes_du_lot(lot.id)}
    assert set(lignes) == {"TH/TH-05", "doc-libre"}
    d = app.vue_dossier(lignes["TH/TH-05"].id)
    assert d.dossier.document.type_document == "these"
    assert [i.nom for i in d.images] == ["p1.png", "p2.png"] and d.images[0].url.startswith("/fichiers/")
    assert app.vue_dossier(lignes["doc-libre"].id).dossier.document.type_document == "memoire"
    with pytest.raises(ActionImpossible, match="dans un dossier"):
        app.televerser_dossiers([("seul.png", page)], "these")


# ---------------------------------------------------------------- routes
@pytest.fixture
def client(app):
    return TestClient(create_app(app), follow_redirects=False)


@respx.mock
def test_routes_bucket(client, app):
    r = client.post("/minio/balayer", headers={"HX-Request": "true"})
    texte = html.unescape(r.text)
    assert r.status_code == 200 and "TH/TH-01" in texte and "3 nouveau(x)" in texte
    assert 'value="TH/TH-01"' in r.text
    simuler()
    r = client.post("/minio/lancer", data={"dossiers": ["TH/TH-01"]}, headers={"HX-Request": "true"})
    assert r.status_code == 204 and r.headers["HX-Redirect"].startswith("/lots/")
    lot_id = r.headers["HX-Redirect"].rsplit("/", 1)[1]
    (ligne,) = app.store.lignes_du_lot(lot_id)
    page = client.get(f"/dossiers/{ligne.id}")
    assert "/minio/objet?uri=s3%3A%2F%2Fimages%2Fpoc%2FTH%2FTH-01%2F001.jpg" in page.text
    image = client.get("/minio/objet", params={"uri": "s3://images/poc/TH/TH-01/001.jpg"})
    assert image.status_code == 200 and image.headers["content-type"] == "image/jpeg"
    assert client.get("/minio/objet", params={"uri": "s3://autre-bucket/x.jpg"}).status_code == 404
    assert "balayage" in client.get("/historique").text


def test_evenements_desactives(client):
    r = client.post("/minio/evenements")
    assert r.status_code == 403 and "désactivé" in r.text


@respx.mock
def test_formulaire_televersement_de_dossiers(client, app):
    simuler()
    page = sim.png()
    r = client.post("/lots", data={"type_document": "these"},
                    files=[("dossiers", ("poc/TH/TH-07/001.png", page, "image/png")),
                           ("dossiers", ("poc/TH/TH-08/001.png", page, "image/png"))])
    assert r.status_code == 303 and r.headers["location"].startswith("/lots/")
    lot_id = r.headers["location"].rsplit("/", 1)[1]
    assert [l.document_id for l in app.store.lignes_du_lot(lot_id)] == ["TH/TH-07", "TH/TH-08"]
    assert 'webkitdirectory' in client.get("/").text


def test_api_bucket(client):
    r = client.post("/api/v1/minio/balayage")
    assert r.status_code == 200 and len(r.json()["dossiers"]) == 3
    assert client.post("/api/v1/minio/lots", json={"identifiants": ["TH/TH-99"]}).status_code == 422


@respx.mock
def test_depot_de_l_application_prioritaire(app):
    """Les services fournissent leur propre dépôt (.env) : celui de l'application, qui a
    constitué les documents, doit lire les images (essai réel du 04/11)."""
    autre = BucketSimule({}, bucket="poc", prefixe="")
    fabrique = app.fabrique
    app.fabrique = lambda s, f: (lambda svc: (setattr(svc, "depot", autre), svc)[1])(fabrique(s, f))
    simuler()
    lot = app.lancer_selection(["TH/TH-01"])
    (ligne,) = app.store.lignes_du_lot(lot.id)
    assert app.vue_dossier(ligne.id).dossier.routage == "auto"
    assert app.depot.lectures and not autre.lectures
