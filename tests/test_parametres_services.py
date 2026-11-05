"""Paramètres de requête des services (config/services.yaml) et réglages par lot (SPEC §3, §6)."""

import json
import shutil

import pytest
import respx
from fastapi.testclient import TestClient

import services_simules as sim
from conftest import RACINE
from humatheque.application import ActionImpossible, Application, EntreeLot, ExecuteurSynchrone
from humatheque.config import Reglages
from humatheque.services import ClientService
from humatheque.services.dewey import classer
from humatheque.store import Store
from humatheque.web import create_app
from services_simules import simuler


@pytest.fixture
def app(tmp_path):
    shutil.copytree(RACINE / "spec", tmp_path / "spec")
    reglages = Reglages(_env_file=None).model_copy(update={"racine": tmp_path, "spec_source": "csv"})
    return Application(store=Store(tmp_path / "data" / "t.db"), reglages=reglages,
                       fabrique=lambda s, f: sim.services(s, f), executeur=ExecuteurSynchrone(), depot=None)


@pytest.fixture
def image(tmp_path):
    p = tmp_path / "page.png"
    p.write_bytes(sim.png())
    return str(p)


def _corps(route, i=0):
    return json.loads(route.calls[i].request.content)


@respx.mock
def test_parametres_envoyes_par_defaut(app, image):
    routes = simuler()
    app.creer_lot(EntreeLot(documents=[[image]]))
    alma, sudoc = _corps(routes["alma"]), _corps(routes["sudoc"])
    assert (alma["match_threshold"], alma["ambiguous_threshold"], alma["max_candidates"]) == (0.78, 0.62, 20)
    assert sudoc["duplicate_threshold"] == 0.78  # config/services.yaml : même seuil que l'agrégation
    assert sudoc["ambiguous_threshold"] == 0.62
    idref = _corps(routes["idref"])
    assert (idref["accept_threshold"], idref["margin_threshold"]) == (0.5, 0.08)  # décision du 05/11
    assert (idref["weight_attrra_source"], idref["weight_references"]) == (0.15, 0.25)  # décision du 04/11
    orgs = _corps(routes["orgs"])
    assert (orgs["accept_threshold"], orgs["allow_remote"]) == (0.72, False)
    dewey = _corps(routes["dewey"])
    assert (dewey["method"], dewey["top_k"]) == ("albert", 5)


@respx.mock
def test_reglages_du_lot_appliques_et_traces(app, image):
    routes = simuler()
    lot = app.creer_lot(EntreeLot(documents=[[image]], reglages_services={
        "idref_personne": {"accept_threshold": "0,7", "margin_threshold": "0.08"},  # 0.08 = défaut : omis
        "dewey": {"method": "local"}}))
    assert lot.parametres["services"] == {"idref_personne": {"accept_threshold": 0.7}, "dewey": {"method": "local"}}
    assert _corps(routes["idref"])["accept_threshold"] == 0.7
    assert _corps(routes["idref"])["weight_name"] == 0.40  # non réglé : configuration
    assert _corps(routes["dewey"])["method"] == "local"
    # requête journalisée dans le dossier (traçabilité)
    (ligne,) = app.store.lignes_du_lot(lot.id)
    d = app.vue_dossier(ligne.id).dossier
    assert d.etapes["idref_personnes"].appels[0].requete["accept_threshold"] == 0.7


@pytest.mark.parametrize("reglages, motif", [
    ({"idref_personne": {"accept_threshold": "1.5"}}, "hors de"),
    ({"idref_personne": {"accept_threshold": "beaucoup"}}, "nombre attendu"),
    ({"dewey": {"method": "magique"}}, "hors de"),
    ({"idref_personne": {"weight_name": "0.5"}}, "non réglable"),
    ({"alma": {"max_candidates": "5"}}, "non réglable"),
])
def test_reglages_refuses(app, image, reglages, motif):
    with pytest.raises(ActionImpossible, match=motif):
        app.creer_lot(EntreeLot(documents=[[image]], reglages_services=reglages))


@respx.mock
def test_dewey_albert_scores_reranker():
    respx.post(f"{sim.DEWEY_URL}/classify").respond(json=sim.fixture("dewey_albert"))
    res, appel = classer(ClientService("dewey", sim.DEWEY_URL), {"title": "x"}, seuil=None)
    assert (res.code, res.statut) == ("930", "au_dessus_seuil") and appel.requete["method"] == "albert"
    res, _ = classer(ClientService("dewey", sim.DEWEY_URL), {"title": "x"}, seuil=0.5)
    assert res.statut == "sous_seuil"  # échelle du reranker : DEWEY_SEUIL à fixer en conséquence


@respx.mock
def test_formulaire_parametres_avances(app, tmp_path):
    client = TestClient(create_app(app), follow_redirects=False)
    page = client.get("/")
    assert "Paramètres avancés" in page.text and 'name="reglage.idref_personne.accept_threshold"' in page.text
    assert 'value="0.5"' in page.text and 'value="albert" checked' in page.text  # choix segmenté
    assert [r["parametre"] for r in client.get("/api/v1/reglables").json()] == [
        "match_threshold", "duplicate_threshold", "accept_threshold", "margin_threshold", "accept_threshold", "method"]
    simuler()
    r = client.post("/lots", data={"type_document": "these", "reglage.idref_personne.accept_threshold": "0.6",
                                   "reglage.dewey.method": "albert"},
                    files=[("images", ("p.png", sim.png(), "image/png"))])
    dossier = client.get(r.headers["location"])
    lot_id = app.store.ligne_dossier(r.headers["location"].rsplit("/", 1)[1]).lot_id
    assert "idref_personne.accept_threshold=0.6" in client.get(f"/lots/{lot_id}").text
    assert dossier.status_code == 200
    r = client.post("/lots", data={"type_document": "these", "reglage.idref_personne.accept_threshold": "2"},
                    files=[("images", ("p.png", sim.png(), "image/png"))])
    assert r.status_code == 422
