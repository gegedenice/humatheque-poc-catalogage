"""Étape 8 (SPEC §3.1, ING-F-003, ING-F-004) : déclencheurs, administration, santé, mot de passe."""

import shutil
from datetime import datetime, timedelta, timezone

import httpx
import pytest
import respx
from pydantic import SecretStr
from fastapi.testclient import TestClient

import services_simules as sim
from conftest import RACINE
from humatheque.application import ActionImpossible, Application, ExecuteurSynchrone, cron
from humatheque.application.administration import ReglagesAdmin
from humatheque.application.planificateur import Planificateur
from humatheque.config import Reglages
from humatheque.store import Store
from humatheque.web import create_app
from services_simules import simuler
from test_ingestion import BucketSimule, bucket

T0 = datetime(2026, 11, 5, 2, 29, 50, tzinfo=timezone.utc)


class Horloge:
    def __init__(self, t=T0):
        self.t = t

    def __call__(self):
        return self.t

    def avancer(self, **kw):
        self.t += timedelta(**kw)


def _app(tmp_path, **reglages):
    shutil.copytree(RACINE / "regles", tmp_path / "regles", ignore=shutil.ignore_patterns("archives"),
                    dirs_exist_ok=True)
    r = Reglages(_env_file=None).model_copy(update={"racine": tmp_path, **reglages})
    return Application(store=Store(tmp_path / "data" / "t.db"), reglages=r,
                       fabrique=lambda s, f: sim.services(s, f), executeur=ExecuteurSynchrone(), depot=bucket())


@pytest.fixture
def app(tmp_path):
    return _app(tmp_path)


def _activer(app, **declencheurs):
    r = app.reglages_admin()
    for nom, valeurs in declencheurs.items():
        setattr(r.declencheurs, nom, getattr(r.declencheurs, nom).model_copy(update=valeurs))
    return app.modifier_reglages_admin(r, "admin")


# ---------------------------------------------------------------- cron
@pytest.mark.parametrize("expr, instant, attendu", [
    ("30 2 * * *", datetime(2026, 11, 5, 2, 30), True),
    ("30 2 * * *", datetime(2026, 11, 5, 2, 31), False),
    ("*/15 8-18 * * 1-5", datetime(2026, 11, 5, 9, 45), True),     # jeudi
    ("*/15 8-18 * * 1-5", datetime(2026, 11, 8, 9, 45), False),    # dimanche
    ("0 0 1 1,7 *", datetime(2026, 7, 1, 0, 0), True),
    ("0 12 * * 7", datetime(2026, 11, 8, 12, 0), True),            # 7 = dimanche
])
def test_cron(expr, instant, attendu):
    assert cron.correspond(expr, instant) is attendu


@pytest.mark.parametrize("expr", ["", "* * *", "60 * * * *", "a * * * *", "*/0 * * * *", "5-1 * * * *"])
def test_cron_invalide(expr):
    with pytest.raises(ValueError):
        cron.analyser(expr)


def test_cron_prochaine():
    assert cron.prochaine("30 2 * * *", datetime(2026, 11, 5, 2, 30)) == datetime(2026, 11, 6, 2, 30)


# ---------------------------------------------------------------- réglages
def test_valeurs_initiales_depuis_env(tmp_path):
    a = _app(tmp_path, vlm_fournisseur="hf", ingestion_auto=True, balayage_intervalle=30,
             planification_cron="30 2 * * *", evenements_delai=90)
    r = a.reglages_admin()
    assert r.fournisseur == "hf" and r.maj_le is None
    assert (r.declencheurs.balayage.actif, r.declencheurs.balayage.intervalle_minutes) == (True, 30)
    assert (r.declencheurs.planification.actif, r.declencheurs.planification.cron) == (True, "30 2 * * *")
    assert (r.declencheurs.evenements.actif, r.declencheurs.evenements.delai_secondes) == (False, 90)


def test_modifier_reglages_et_defauts_du_formulaire(app):
    r = app.reglages_admin()
    r.fournisseur = "albert"
    r.reglages_services = {"idref_personne": {"accept_threshold": "0.6"}}
    r = app.modifier_reglages_admin(r, "admin")
    assert r.auteur == "admin" and r.maj_le and r.reglages_services == {"idref_personne": {"accept_threshold": 0.6}}
    assert app.accueil().fournisseur_defaut == "albert"
    assert next(x for x in app.reglables() if x.parametre == "accept_threshold").defaut == 0.6


@pytest.mark.parametrize("modif, motif", [
    ({"fournisseur": "magique"}, "fournisseur"),
    ({"reglages_services": {"idref_personne": {"accept_threshold": "3"}}}, "hors de"),
])
def test_reglages_invalides(app, modif, motif):
    with pytest.raises(ActionImpossible, match=motif):
        app.modifier_reglages_admin(app.reglages_admin().model_copy(update=modif), "admin")


def test_cron_invalide_refuse(app):
    with pytest.raises(ActionImpossible, match="cron"):
        _activer(app, planification={"actif": True, "cron": "30 25 * * *"})


def test_jeton_genere_a_l_activation(app):
    r = _activer(app, evenements={"actif": True})
    assert len(r.declencheurs.evenements.jeton) > 20
    assert app.renouveler_jeton("admin").declencheurs.evenements.jeton != r.declencheurs.evenements.jeton


def test_s3_requis(app):
    app.depot = None
    with pytest.raises(ActionImpossible, match="S3"):
        _activer(app, balayage={"actif": True})


# ---------------------------------------------------------------- planificateur
@respx.mock
def test_balayage_periodique(app):
    simuler()
    _activer(app, balayage={"actif": True, "intervalle_minutes": 30})
    h = Horloge()
    p = Planificateur(app, horloge=h)
    lot_id = p.tick()
    lot = app.store.lot(lot_id)
    assert lot.declencheur == "periodique" and lot.nb_documents == 3
    assert lot.parametres["fournisseur"] == app.reglages_admin().fournisseur
    h.avancer(minutes=10)
    assert p.tick() is None                 # intervalle non écoulé
    h.avancer(minutes=21)
    assert p.tick() == "rien"               # plus de nouveau dossier
    assert app.historique().ingestions[0].declencheur == "periodique"


@respx.mock
def test_planification_une_fois_par_minute(app):
    simuler()
    _activer(app, planification={"actif": True, "cron": "30 2 * * *"})
    h = Horloge()
    p = Planificateur(app, horloge=h)
    assert p.tick() is None                 # 02:29:50
    h.avancer(seconds=15)                   # 02:30:05
    assert app.store.lot(p.tick()).declencheur == "planifie"
    h.avancer(seconds=30)                   # 02:30:35 : même minute
    assert p.tick() is None
    assert p.etat().prochaine_planification.day == 6


@respx.mock
def test_evenements_stabilisation(app):
    simuler()
    _activer(app, evenements={"actif": True, "delai_secondes": 60})
    h = Horloge()
    p = Planificateur(app, horloge=h)
    p.signaler_evenement()
    h.avancer(seconds=30)
    p.signaler_evenement()                  # nouvel objet déposé : le délai repart
    h.avancer(seconds=45)
    assert p.tick() is None
    h.avancer(seconds=20)
    assert app.store.lot(p.tick()).declencheur == "evenement"
    assert p.etat().evenement_en_attente is None


def test_attente_si_lot_automatique_en_cours(app):
    _activer(app, balayage={"actif": True})
    from humatheque.application import EntreeLot

    app.executeur = type("Inerte", (), {"submit": lambda self, *a, **k: None})()
    app.creer_lot(EntreeLot(documents=[["x"]], declencheur="periodique"))
    assert Planificateur(app, horloge=Horloge()).tick() == "attente : lot automatique en cours"


def test_echec_du_bucket_note(app):
    _activer(app, balayage={"actif": True})
    app.depot = BucketSimule({}, panne=True)
    p = Planificateur(app, horloge=Horloge())
    assert p.tick() == "echec" and "échec" in p.etat().derniers_resultats[0]


@respx.mock
def test_dossier_modifie_signale_non_retraite(app):
    simuler()
    app.lancer_selection(["TH/TH-01"])
    app.depot.objets["poc/TH/TH-01/003.png"] = sim.png()
    v = {l.document.id: l for l in app.balayer().dossiers}
    assert v["TH/TH-01"].modifie and not v["TH/TH-01"].nouveau
    assert not v["MM/MM-01"].modifie
    lot = app.traiter_nouveaux("periodique")
    assert "TH/TH-01" not in [l.document_id for l in app.store.lignes_du_lot(lot.id)]


@respx.mock
def test_lots_automatiques_depuis(app):
    simuler()
    avant = datetime.now(timezone.utc) - timedelta(seconds=1)
    app.lancer_selection(["MM/MM-01"])                  # manuel : non compté
    app.traiter_nouveaux("planifie")
    assert [l.declencheur for l in app.lots_automatiques_depuis(avant)] == ["planifie"]


# ---------------------------------------------------------------- santé
@respx.mock
def test_sante(tmp_path):
    a = _app(tmp_path, alma_checker_api_url="https://alma.test", sudoc_checker_api_url="https://sudoc.test",
             idref_qualinka_api_url="", dewey_classification_api_url="https://dewey.test",
             hf_api_url="https://hf.test/v1/chat/completions", vlm_fournisseur="hf")
    respx.get("https://alma.test/health").respond(json={"ok": True})
    respx.get("https://sudoc.test/health").respond(503)
    respx.get("https://dewey.test/health").mock(side_effect=httpx.ConnectError("refusé"))
    respx.get("https://hf.test/v1/models").respond(json={"data": []})
    s = {x.composant: x for x in a.sante()}
    assert s["alma-checker"].ok and s["sudoc-checker"].ok is False and s["dewey-classifier"].ok is False
    assert s["idref-linker"].ok is None and "non configurée" in s["idref-linker"].detail
    assert s["VLM (hf)"].ok and s["stockage S3"].ok and s["règles"].ok
    assert "Grist" not in s


# ---------------------------------------------------------------- web : admin, évènements, mot de passe
@respx.mock
def test_page_admin_et_reglages(app):
    c = TestClient(create_app(app), follow_redirects=False)
    respx.get(url__regex=r".*/health").respond(json={"ok": True})
    page = c.get("/admin")
    assert page.status_code == 200 and "Déclencheurs" in page.text and 'hx-get="/admin/sante"' in page.text
    r = c.post("/admin/reglages", data={"fournisseur": "hf", "balayage_actif": "on", "balayage_intervalle": "45",
                                        "evenements_actif": "on", "evenements_delai": "30",
                                        "planification_cron": "", "auteur": "Camille",
                                        "reglage.idref_personne.accept_threshold": "0.5"})
    assert r.status_code == 303
    reg = app.reglages_admin()
    assert reg.declencheurs.balayage.intervalle_minutes == 45 and reg.declencheurs.evenements.actif
    page = c.get("/admin")
    assert f"/minio/evenements?jeton={reg.declencheurs.evenements.jeton}" in page.text
    assert c.post("/admin/reglages", data={"planification_actif": "on", "planification_cron": "x",
                                           "auteur": "Camille"}).status_code == 422
    assert c.get("/api/v1/admin/reglages").json()["declencheurs"]["balayage"]["actif"] is True
    assert c.get("/api/v1/admin/planificateur").json()["actif"] is False


@respx.mock
def test_route_evenements_jeton(app):
    simuler()
    c = TestClient(create_app(app), follow_redirects=False)
    jeton = _activer(app, evenements={"actif": True, "delai_secondes": 0}).declencheurs.evenements.jeton
    assert c.post("/minio/evenements").status_code == 401
    assert c.post("/minio/evenements?jeton=faux").status_code == 401
    r = c.post(f"/minio/evenements?jeton={jeton}", json={"EventName": "s3:ObjectCreated:Put"})
    assert r.status_code == 202
    plan = c.app.state.planificateur
    assert plan.evenement_en_attente is not None
    assert app.store.lot(plan.tick()).declencheur == "evenement"


@respx.mock
def test_bandeau_nouveaux_lots(app):
    simuler()
    c = TestClient(create_app(app), follow_redirects=False)
    premiere = c.get("/")
    assert "depuis votre dernière visite" not in premiere.text and "derniere_visite" in premiere.cookies
    app.traiter_nouveaux("planifie")
    assert "1 lot(s) automatique(s) depuis votre dernière visite" in c.get("/").text
    assert "depuis votre dernière visite" not in c.get("/").text


def test_mot_de_passe_desactive_par_defaut(app):
    c = TestClient(create_app(app), follow_redirects=False)
    assert c.get("/").status_code == 200 and c.get("/api/v1/spec").status_code == 200


def test_mot_de_passe_active(tmp_path):
    a = _app(tmp_path, mot_de_passe=SecretStr("s3cret"))
    c = TestClient(create_app(a), follow_redirects=False)
    assert c.get("/").status_code == 303 and c.get("/").headers["location"] == "/login?suite=/"
    assert c.get("/api/v1/spec").status_code == 401
    assert c.get("/static/css/app.css").status_code == 200
    assert c.post("/minio/evenements").status_code == 403    # ouverte, protégée par son jeton
    assert c.get("/api/v1/spec", headers={"Authorization": "Bearer s3cret"}).status_code == 200
    assert c.post("/login", data={"mot_de_passe": "faux", "suite": "/admin"}).status_code == 401
    r = c.post("/login", data={"mot_de_passe": "s3cret", "suite": "/admin"})
    assert r.status_code == 303 and r.headers["location"] == "/admin"
    assert c.get("/admin").status_code == 200
    c.get("/logout")
    assert c.get("/admin").status_code == 303


def test_fournisseurs_ouverts_albert_par_defaut(tmp_path):
    """Décision du 11/11 (VLM-T-006) : OpenAI retiré, Albert par défaut avec le modèle de .env."""
    from humatheque.application import FOURNISSEURS
    from humatheque.config import Reglages
    from humatheque.extraction.fournisseurs import fournisseur, table_fournisseurs

    assert FOURNISSEURS[0] == "albert" and "openai" not in FOURNISSEURS
    r = Reglages(_env_file=None, albert_model="modele-du-env")
    assert r.vlm_fournisseur == "albert" and "openai" not in table_fournisseurs(r)
    assert fournisseur("", r).modele == "modele-du-env"
    a = _app(tmp_path, albert_model="modele-du-env")
    assert a.fournisseur_effectif(None) == ("albert", "modele-du-env")
    # ancien réglage d'administration « openai » : retour au défaut
    a.store.ecrire_reglage_admin("lots_automatiques", {**a.reglages_admin().model_dump(mode="json", exclude={"maj_le", "auteur"}),
                                              "fournisseur": "openai"}, "C")
    assert a.reglages_admin().fournisseur == "albert"
