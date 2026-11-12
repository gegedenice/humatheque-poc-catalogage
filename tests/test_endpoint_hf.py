"""Fournisseur custom-hf-endpoint (décision du 12/11) : endpoint HF dédié en
scale-to-zero ; état et réveil par l'API de gestion, attente avant l'inférence,
second essai après un 503. Aucun appel réseau réel (respx)."""

import httpx
import pytest
import respx
from pydantic import SecretStr

from humatheque.config import Reglages
from humatheque.extraction import endpoint_hf
from humatheque.extraction.fournisseurs import (
    FournisseurEndpointHF, InferenceIndisponible, fournisseur, lister_modeles, nom_fournisseur,
)

GESTION = "https://gestion.test/v2/endpoint/espace/lift"
URL = "https://lift.test/v1/chat/completions"
REPONSE = {"choices": [{"message": {"content": '{"title": "T"}'}}]}


def _r(**k):
    return Reglages(_env_file=None, **{"hf_endpoint_api_url": URL, "hf_endpoint_api_model": "org/lift-GGUF",
                                       "hf_endpoint_name": "lift", "hf_endpoint_namespace": "espace",
                                       "hf_endpoints_api_base": "https://gestion.test/v2/endpoint",
                                       "hf_token": SecretStr("jeton"), "hf_endpoint_attente": 60, **k})


def _etat(nom, url="https://lift.test"):
    return httpx.Response(200, json={"status": {"state": nom, "url": url, "readyReplica": 1 if nom == "running" else 0,
                                                 "targetReplica": 1}, "healthRoute": "/health"})


def test_nom_et_alias():
    assert nom_fournisseur("vlm") == "vlm-local" and nom_fournisseur("HF") == "hf"
    f = fournisseur("custom-hf-endpoint", _r())
    assert isinstance(f, FournisseurEndpointHF) and f.modele == "org/lift-GGUF" and f.url == URL
    assert lister_modeles("custom-hf-endpoint", _r()) == []  # seulement le modèle de .env
    assert fournisseur("vlm", _r(vlm_api_url="https://v.test/v1/chat/completions")).nom == "vlm-local"


@respx.mock
def test_etat_et_reveil():
    respx.get(GESTION).mock(side_effect=[_etat("paused"), _etat("paused"), _etat("initializing")])
    resume = respx.post(f"{GESTION}/resume").respond(200, json={})
    r = _r()
    e = endpoint_hf.etat(r)
    assert (e.etat, e.ton, e.pret) == ("paused", "veille", False)
    e = endpoint_hf.reveiller(r)
    assert resume.called and e.etat == "initializing"
    assert respx.calls[0].request.headers["Authorization"] == "Bearer jeton"


@respx.mock
def test_veille_puis_inference(monkeypatch):
    """Endpoint en veille : réveil par la route de santé, attente, puis inférence ;
    les appels à l'API de gestion sont journalisés avec l'extraction."""
    respx.get(GESTION).mock(side_effect=[_etat("scaledToZero"), _etat("scaledToZero"), _etat("initializing"),
                                         _etat("running"), _etat("running")])
    sante = respx.get("https://lift.test/health").respond(503)
    infer = respx.post(URL).respond(json=REPONSE)
    f = fournisseur("custom-hf-endpoint", _r())
    attentes = []
    original = endpoint_hf.attendre_pret
    monkeypatch.setattr(endpoint_hf, "attendre_pret",
                        lambda r, journal=None, **k: original(r, journal=journal, attendre=attentes.append))
    contenu, appel = f.appeler("prompt", ["data:image/jpeg;base64,AA"])
    assert contenu == '{"title": "T"}' and sante.called and infer.call_count == 1 and attentes
    gestion = f.appels_prealables()
    assert gestion and all(a.service == "vlm:custom-hf-endpoint:gestion" for a in gestion)
    assert f.appels_prealables() == []  # rendus une seule fois


@respx.mock
def test_503_puis_second_essai():
    respx.get(GESTION).mock(side_effect=[_etat("running"), _etat("running"), _etat("running")])
    infer = respx.post(URL).mock(side_effect=[httpx.Response(503, text="endormi"), httpx.Response(200, json=REPONSE)])
    f = fournisseur("custom-hf-endpoint", _r())
    contenu, _ = f.appeler("prompt", ["x"])
    assert contenu and infer.call_count == 2
    assert any(a.statut_http == 503 for a in f.appels_prealables())


@respx.mock
def test_pas_pret_dans_le_delai():
    respx.get(GESTION).mock(return_value=_etat("initializing"))
    respx.get("https://lift.test/health").respond(503)
    f = fournisseur("custom-hf-endpoint", _r(hf_endpoint_attente=0))
    with pytest.raises(InferenceIndisponible, match="non prêt"):
        f.appeler("prompt", ["x"])


def test_page_et_admin(tmp_path):
    from fastapi.testclient import TestClient
    from humatheque.application import Application, ExecuteurSynchrone
    from humatheque.store import Store
    from humatheque.web import create_app
    import shutil
    from conftest import RACINE

    shutil.copytree(RACINE / "regles", tmp_path / "regles", ignore=shutil.ignore_patterns("archives"))
    app = Application(store=Store(tmp_path / "t.db"), reglages=_r().model_copy(update={"racine": tmp_path}),
                      executeur=ExecuteurSynchrone(), depot=None)
    c = TestClient(create_app(app))
    with respx.mock:
        respx.get(GESTION).mock(side_effect=[_etat("scaledToZero"), _etat("scaledToZero"), _etat("initializing")])
        respx.get("https://lift.test/health").respond(503)
        assert "scaledToZero" in c.get("/admin/endpoint-hf").text
        r = c.post("/admin/endpoint-hf/reveiller").text
        assert "initializing" in r and "redémarrage demandé" in r
    assert c.get("/admin/endpoint-hf", params={"fournisseur": "albert"}).text == ""
