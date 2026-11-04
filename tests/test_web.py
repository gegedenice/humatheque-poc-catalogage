"""Routes HTML et JSON (SPEC §9, §11 étape 6) : mêmes fonctions de application/."""

import html
import shutil

import pytest
import respx
from fastapi.testclient import TestClient

import services_simules as sim
from conftest import RACINE
from humatheque.application import Application, ExecuteurSynchrone
from humatheque.config import Reglages
from humatheque.store import Store
from humatheque.web import create_app
from services_simules import simuler


@pytest.fixture
def app(tmp_path):
    shutil.copytree(RACINE / "spec", tmp_path / "spec")
    reglages = Reglages(_env_file=None).model_copy(update={"racine": tmp_path, "spec_source": "csv"})
    return Application(store=Store(tmp_path / "data" / "t.db"), reglages=reglages,
                       fabrique=lambda s, f: sim.services(s, f), executeur=ExecuteurSynchrone())


@pytest.fixture
def client(app):
    return TestClient(create_app(app), follow_redirects=False)


def _televerser(client, n=1, un_seul=False):
    fichiers = [("images", (f"p{i}.png", sim.png(), "image/png")) for i in range(n)]
    donnees = {"type_document": "these", "fournisseur": "vlm"}
    if un_seul:
        donnees["un_seul_document"] = "true"
    return client.post("/lots", data=donnees, files=fichiers)


# ---------------------------------------------------------------- pages
def test_accueil(client):
    r = client.get("/")
    texte = html.unescape(r.text)
    assert r.status_code == 200
    assert "Nouveau traitement" in texte and "Spécification chargée" in texte
    assert "aucune erreur bloquante" in texte and "Aucun traitement pour l'instant" in texte
    assert '<script src="/static/vendor/htmx.min.js"' in r.text


def test_statiques(client):
    assert client.get("/static/css/app.css").status_code == 200
    assert client.get("/static/vendor/htmx.min.js").status_code == 200
    assert client.get("/static/vendor/openseadragon/openseadragon.min.js").status_code == 200


def test_recharger_spec_fragment_htmx(client):
    r = client.post("/spec/recharger", headers={"HX-Request": "true"})
    assert r.status_code == 200 and r.text.lstrip().startswith('<section id="etat-spec"')


@respx.mock
def test_mode1_televersement_redirige_vers_le_dossier(client):
    simuler()
    r = _televerser(client, n=2, un_seul=True)
    assert r.status_code == 303 and r.headers["location"].startswith("/dossiers/D")
    page = client.get(r.headers["location"])
    assert page.status_code == 200
    assert "Contribution à l'étude de la démocratie en Afrique" in html.unescape(page.text)
    assert 'id="visionneuse"' in page.text and "/fichiers/" in page.text
    assert "700 #1$3026940345$4070" in page.text
    assert "hx-trigger=\"every 3s\"" not in page.text  # traitement terminé : pas de rafraîchissement
    # téléchargements
    dossier = r.headers["location"]
    t = client.get(f"{dossier}/telecharger/notice.txt")
    assert t.status_code == 200 and "attachment" in t.headers["content-disposition"]
    assert client.get(f"{dossier}/telecharger/source.iso").status_code == 404


@respx.mock
def test_mode2_lot_file_filtres_et_exports(client):
    simuler(alma=sim.alma("academic_record_found", ppn="229969437"))
    r = _televerser(client, n=3)
    assert r.headers["location"].startswith("/lots/")
    lot = r.headers["location"]
    page = client.get(lot)
    assert "File d'exceptions à reprendre" in html.unescape(page.text)
    assert page.text.count(">Reprendre</a>") == 3 and "229969437" in page.text
    # filtre en fragment HTMX, indicateurs en hx-swap-oob
    frag = client.get(f"{lot}/fragment?filtre=incidents", headers={"HX-Request": "true"})
    assert 'id="lot-dyn"' in frag.text and 'hx-swap-oob="true"' in frag.text
    assert "Aucun document dans cette catégorie" in frag.text
    assert client.get(f"{lot}/rapport.csv").text.startswith("document,dossier,routage")
    assert client.get(f"{lot}/notices.txt").status_code == 200


@respx.mock
def test_resolution_par_formulaire(client, app):
    simuler(alma=sim.alma("off_profile_match_only", ppn="111111111"))
    dossier = _televerser(client).headers["location"]
    page = client.get(dossier)
    assert "Déclarer un faux positif" in page.text and "Traité hors outil" in page.text
    simuler()
    r = client.post(f"{dossier}/faux-positif", data={"service": "alma", "commentaire": "autre ouvrage",
                                                     "auteur": "Camille"})
    assert r.status_code == 303 and r.cookies.get("catalogueur") == "Camille"
    client.cookies.set("catalogueur", "Camille")
    page = client.get(dossier)
    assert "700 #1$3026940345$4070" in page.text and "Camille" in page.text


@respx.mock
def test_action_impossible_htmx_et_page(client):
    simuler()
    dossier = _televerser(client).headers["location"]
    r = client.post(f"{dossier}/hors-outil", data={"auteur": "x", "commentaire": " "},
                    headers={"HX-Request": "true"})
    assert r.status_code == 422 and "commentaire est requis" in r.text
    r = client.post(f"{dossier}/hors-outil", data={"auteur": " ", "commentaire": "ok"})
    assert r.status_code == 422 and "Action impossible" in r.text


def test_introuvable(client):
    assert client.get("/dossiers/D-inconnu").status_code == 404
    assert client.get("/lots/L-inconnu").status_code == 404
    assert client.get("/fichiers/../t.db").status_code == 404


def test_lot_sans_image(client):
    r = client.post("/lots", data={"type_document": "these"})
    assert r.status_code == 422


@respx.mock
def test_historique(client):
    simuler()
    _televerser(client, n=2)
    r = client.get("/historique")
    assert r.status_code == 200 and "traitement" in r.text and "manuel" in r.text


# ---------------------------------------------------------------- API JSON
def test_api_spec(client):
    r = client.get("/api/v1/spec")
    assert r.status_code == 200 and r.json()["nb_erreurs"] == 0


@respx.mock
def test_api_lot_dossier_resolution(client, tmp_path):
    simuler(idref_statut="low_confidence")
    image = tmp_path / "p.png"
    image.write_bytes(sim.png())
    r = client.post("/api/v1/lots", json={"type_document": "these", "documents": [[str(image)]]})
    assert r.status_code == 201
    lot = client.get(f"/api/v1/lots/{r.json()['id']}").json()
    assert lot["indicateurs"]["documents"] == 1 and lot["file"][0]["routage"] == "décision"
    dossier_id = lot["file"][0]["dossier_id"]
    d = client.get(f"/api/v1/dossiers/{dossier_id}").json()
    i = next(i for i, e in enumerate(d["dossier"]["exceptions"]) if e["objet"].startswith("author"))
    r = client.post(f"/api/v1/dossiers/{dossier_id}/resolutions/ppn",
                    json={"auteur": "api", "exception": i, "ppn": "026940345"})
    assert r.status_code == 200 and "700 #1$3026940345$4070" in r.json()["dossier"]["notice_texte"]
    r = client.post(f"/api/v1/dossiers/{dossier_id}/resolutions/ppn", json={"auteur": "api", "exception": i,
                                                                             "ppn": "026940345"})
    assert r.status_code == 422 and "déjà résolue" in r.json()["detail"]
    assert client.get("/api/v1/dossiers/inconnu").status_code == 404
    assert client.post(f"/api/v1/dossiers/{dossier_id}/relancer", json={"depuis": "dewey"}).status_code == 200
    assert client.get("/api/v1/historique").json()["lots"][0]["lot"]["id"]


def test_api_documentee(client):
    assert "/api/v1/lots" in client.get("/api/v1/openapi.json").json()["paths"]
