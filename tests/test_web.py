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
    shutil.copytree(RACINE / "regles", tmp_path / "regles", ignore=shutil.ignore_patterns("archives"))
    reglages = Reglages(_env_file=None).model_copy(update={"racine": tmp_path})
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
    """Accueil : bandeau, entrées de menu (chargement manuel, lots, historique, admin),
    derniers traitements, balayage du bucket ; le formulaire est sur /traiter."""
    r = client.get("/")
    texte = html.unescape(r.text)
    assert r.status_code == 200
    assert "Humathèque" in texte and "POC catalogage" in texte
    for w in ["Lots traités", "Historique", "Chargement manuel", "Administration"]:
        assert w in texte, w
    assert "Derniers traitements" in texte and "Aucun traitement pour l'instant" in texte and "Bucket S3" in texte
    assert 'action="/lots"' not in r.text
    assert '<script src="/static/vendor/htmx.min.js"' in r.text
    t = html.unescape(client.get("/traiter").text)
    assert 'action="/lots"' in t and "Règles chargées" in t and "aucune erreur bloquante" in t
    assert "Derniers traitements" not in t


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
    t = client.get(f"{dossier}/telecharger/notice.mrc")
    assert t.status_code == 200 and t.headers["content-disposition"] == 'attachment; filename="001_p0.mrc"'
    assert t.content.endswith(b"\x1d")  # ISO 2709 (et plus de .png dans le nom)
    x = client.get(f"{dossier}/telecharger/notice.xml")
    assert x.headers["content-disposition"].endswith('.xml"') and b"<record" in x.content
    assert client.get(f"{dossier}/telecharger/source.mrc").status_code == 404
    assert client.get(f"{dossier}/telecharger/notice.txt").status_code == 404  # plus de WinIBW


@respx.mock
def test_mode2_lot_file_filtres_et_exports(client):
    simuler(alma=sim.alma("ambiguous_academic_candidate", ppn="229969437"))
    r = _televerser(client, n=3)
    assert r.headers["location"].startswith("/lots/")
    lot = r.headers["location"]
    page = client.get(lot)
    assert "Documents à reprendre" in html.unescape(page.text)
    assert page.text.count(">Reprendre</a>") == 3 and "229969437" in page.text
    # filtre en fragment HTMX, indicateurs en hx-swap-oob
    frag = client.get(f"{lot}/fragment?filtre=incidents", headers={"HX-Request": "true"})
    assert 'id="lot-dyn"' in frag.text and 'hx-swap-oob="true"' in frag.text
    assert "Aucun document dans cette catégorie" in frag.text
    assert client.get(f"{lot}/rapport.csv").text.startswith("document,dossier,issue,regle_de_decision")
    m = client.get(f"{lot}/notices.mrc")
    assert m.status_code == 200 and m.headers["content-type"] == "application/marc"
    assert b"<collection" in client.get(f"{lot}/notices.xml").content
    assert client.get(f"{lot}/notices.txt").status_code == 404


@respx.mock
def test_notice_alma_certaine_sans_a_trancher(client):
    """Notice au-dessus de match_threshold : arrêt terminé, sans action proposée (05/11) ;
    Bilan : étape, motif du pipeline, PPN (11/11) ; pas d'onglet Notice sans notice."""
    simuler(alma=sim.alma("academic_record_found", ppn="229969437"))
    page = client.get(_televerser(client).headers["location"])
    texte = html.unescape(page.text)
    assert 'id="bilan"' in texte and "Alma : notice déjà dans Alma" in texte and "229969437" in texte
    assert "Faux positif" not in texte and "traité hors outil" not in texte and "Pas de notice" not in texte
    assert 'id="onglet-notice"' not in texte and 'id="onglet-extraction"' in texte


@respx.mock
def test_resolution_par_formulaire(client, app):
    simuler(alma=sim.alma("off_profile_match_only", ppn="111111111"))
    dossier = _televerser(client).headers["location"]
    page = html.unescape(client.get(dossier).text)
    # Bilan (11/11) : étape et motif du pipeline, notice(s) candidate(s), actions sur place
    bilan = page[page.index('id="bilan"'):page.index('id="onglets-dossier"')]
    assert "Alma : candidat Alma à vérifier" in bilan and "Notice(s) Alma candidate(s)" in bilan
    assert "Faux positif : poursuivre" in bilan and "Confirmer : notice déjà présente" in bilan \
        and "traité hors outil" in bilan
    assert 'id="service-alma"' in page and "Parcours du pipeline" in page \
        and "ambiguous_academic_candidate OU off_profile_match_only" in page
    assert 'aria-selected="true">Services' in page.replace("\n", "")
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


def test_page_lots_vide(client):
    r = client.get("/lots")
    assert r.status_code == 200 and "Aucun lot pour l'instant" in html.unescape(r.text)
    assert 'href="/lots" class="rail-lien" aria-current="page"' in r.text


@respx.mock
def test_page_lots_filtres(client):
    """Liste des lots (mode 2) : filtres état / type, lots à reprendre, API JSON."""
    simuler()
    _televerser(client, n=2)
    page = client.get("/lots")
    assert page.status_code == 200 and "Ouvrir</a>" in page.text and "2 thèse(s)" in html.unescape(page.text)
    assert "Aucun lot pour ces filtres." in html.unescape(client.get("/lots?type_document=memoire").text)
    assert "Aucun lot pour ces filtres." in html.unescape(client.get("/lots?etat=en_cours").text)
    vue = client.get("/api/v1/lots", params={"etat": "termines"}).json()
    assert vue["total"] == 1 and len(vue["lots"]) == 1 and vue["declencheurs"] == ["manuel"]
    lot = vue["lots"][0]
    assert ([r["lot"]["id"] for r in vue["a_reprendre"]] == [lot["lot"]["id"]]) == (lot["indicateurs"]["en_attente"] > 0)
    assert client.get("/api/v1/lots", params={"etat": "inconnu"}).status_code == 422


# ---------------------------------------------------------------- API JSON
def test_api_spec(client):
    r = client.get("/api/v1/spec")
    assert r.status_code == 200 and r.json()["nb_erreurs"] == 0


@respx.mock
def test_apercu_documents_passes_seuls(client, app):
    """Lot : documents en auto listés avec un lien « Voir » ; aperçu = image + notice, sans éditeur."""
    simuler()
    lot = _televerser(client, n=2).headers["location"]
    page = html.unescape(client.get(lot).text)
    assert "Documents terminés" in page and page.count('">Voir</a>') == 2 and "/apercu\">Voir" not in page
    lot_id = lot.rsplit("/", 1)[1]
    dossier_id = app.vue_lot(lot_id).passes[0].dossier_id
    apercu = client.get(f"/dossiers/{dossier_id}/apercu")
    assert apercu.status_code == 200
    assert 'id="visionneuse"' in apercu.text and 'id="editeur-notice"' not in apercu.text
    texte = html.unescape(apercu.text)
    assert "Notice produite" in texte and ("notice générée" in texte or "version 1" in texte)
    assert '<span class="text-primaire font-semibold">700</span>' in apercu.text
    assert "Ouvrir le dossier complet" in texte
    vue = client.get(f"/api/v1/dossiers/{dossier_id}/apercu").json()
    assert vue["routage"] == "terminé" and vue["notice"].splitlines()[0][:3].isdigit()


@respx.mock
def test_apercu_arret_sans_notice(client):
    """Arrêt sur notice Alma certaine : aperçu sans notice, arrêt affiché en vert."""
    simuler(alma=sim.alma("academic_record_found", ppn="229969437"))
    page = client.get(_televerser(client).headers["location"] + "/apercu")
    texte = html.unescape(page.text)
    assert "Arrêt sans intervention" in texte and "229969437" in texte and "Pas de notice produite" in texte


@respx.mock
def test_formulaire_idref_un_seul_bouton(client, app):
    """Choix IdRef : présentation par autorité, un seul bouton de validation pour tous."""
    simuler(idref_statut="low_confidence")
    dossier = _televerser(client).headers["location"]
    page = client.get(dossier).text
    assert page.count('action="/dossiers/') and page.count('id="choix-idref"') == 1
    assert page.count("Valider les choix IdRef") == 1 and ">Valider</button>" not in page
    d = app.vue_dossier(dossier.rsplit("/", 1)[1]).dossier
    idref = [i for i, e in enumerate(d.exceptions) if e.etape.startswith("idref")]
    auteur_i = next(i for i in idref if d.exceptions[i].objet.startswith("author"))
    autre = next(i for i in idref if i != auteur_i)
    # PPN saisi prioritaire sur le bouton radio ; une autorité laissée sans choix reste ouverte
    r = client.post(f"{dossier}/ppns", data={"auteur": "Camille", f"ppn_{auteur_i}": "sans-lien",
                                             f"saisie_{auteur_i}": "026940345", f"ppn_{autre}": "sans-lien"})
    assert r.status_code == 303
    d = app.vue_dossier(d.id)
    assert d.exceptions_ouvertes == len(idref) - 2
    assert "700 #1$3026940345$4070" in d.dossier.notice_texte.splitlines()
    assert client.post(f"{dossier}/ppns", data={"auteur": "Camille"}).status_code == 422


@respx.mock
def test_api_lot_dossier_resolution(client, tmp_path):
    simuler(idref_statut="low_confidence")
    image = tmp_path / "p.png"
    image.write_bytes(sim.png())
    r = client.post("/api/v1/lots", json={"type_document": "these", "documents": [[str(image)]]})
    assert r.status_code == 201
    lot = client.get(f"/api/v1/lots/{r.json()['id']}").json()
    assert lot["indicateurs"]["documents"] == 1 and lot["file"][0]["routage"] == "à reprendre"
    dossier_id = lot["file"][0]["dossier_id"]
    d = client.get(f"/api/v1/dossiers/{dossier_id}").json()
    i = next(i for i, e in enumerate(d["dossier"]["exceptions"]) if e["objet"].startswith("author"))
    r = client.post(f"/api/v1/dossiers/{dossier_id}/resolutions/ppn",
                    json={"auteur": "api", "exception": i, "ppn": "026940345"})
    assert r.status_code == 200 and "700 #1$3026940345$4070" in r.json()["dossier"]["notice_texte"]
    r = client.post(f"/api/v1/dossiers/{dossier_id}/resolutions/ppn", json={"auteur": "api", "exception": i,
                                                                             "ppn": "026940345"})
    assert r.status_code == 422 and "déjà résolue" in r.json()["detail"]
    restantes = [j for j, e in enumerate(d["dossier"]["exceptions"]) if e["etape"].startswith("idref") and j != i]
    r = client.post(f"/api/v1/dossiers/{dossier_id}/resolutions/ppns",
                    json={"auteur": "api", "choix": {str(j): None for j in restantes}})
    assert r.status_code == 200 and all(e["resolue"] for e in r.json()["dossier"]["exceptions"]
                                        if e["etape"].startswith("idref"))
    assert client.get("/api/v1/dossiers/inconnu").status_code == 404
    assert client.post(f"/api/v1/dossiers/{dossier_id}/relancer", json={"depuis": "dewey"}).status_code == 200
    assert client.get("/api/v1/historique").json()["lots"][0]["lot"]["id"]


def test_api_documentee(client):
    assert "/api/v1/lots" in client.get("/api/v1/openapi.json").json()["paths"]


# ---------------------------------------------------------------- notice éditable (étape 6 ter)
@respx.mock
def test_api_notice(client):
    simuler()
    dossier = _televerser(client).headers["location"].rsplit("/", 1)[1]
    page = client.get(f"/dossiers/{dossier}")
    assert 'id="editeur-notice"' in page.text and "/static/vendor/monaco/vs/loader.js" in page.text
    assert "/static/js/editeur.js" in client.get("/").text  # îlots chargés partout (navigation hx-boost)
    n = client.get(f"/api/v1/notices/{dossier}").json()
    assert n["version"] == 1 and n["statut"] == "générée"
    m = client.post(f"/api/v1/notices/{dossier}/validation", json={"texte": "200 1#$a"}).json()
    assert m[0]["gravite"] == "erreur" and m[0]["ligne"] == 1
    r = client.put(f"/api/v1/notices/{dossier}", json={"texte": n["texte"] + "\n200 1#$a", "auteur": "cat",
                                                       "version": 1})
    assert r.status_code == 200 and r.json()["version"] == 2
    assert client.put(f"/api/v1/notices/{dossier}", json={"texte": "x", "auteur": "cat", "version": 1}).status_code == 409
    r = client.post(f"/api/v1/notices/{dossier}/valider", json={"auteur": "cat"})
    assert r.status_code == 422 and r.json()["marqueurs"][0]["message"] == "sous-zone $a sans valeur"
    assert len(client.get(f"/api/v1/notices/{dossier}/versions").json()) == 2
    v = client.get(f"/dossiers/{dossier}/versions")
    assert "Versions (2)" in v.text


@respx.mock
def test_fragment_dossier_termine_recharge_la_page(client):
    simuler()
    dossier = _televerser(client).headers["location"]
    r = client.get(f"{dossier}/fragment", headers={"HX-Request": "true"})
    assert r.headers.get("HX-Refresh") == "true"


@respx.mock
def test_retours_et_documents_traites(client):
    simuler()
    dossier = _televerser(client).headers["location"]
    page = html.unescape(client.get(dossier).text)
    lot = page.split('href="/lots/')[1].split('"')[0]
    assert "← Retour au lot" in page and "← Tous les lots" in page
    assert "← Tous les lots" in html.unescape(client.get(f"/lots/{lot}").text)
    lots = html.unescape(client.get("/lots").text)
    assert "Documents traités" in lots and "dans les lots affichés" in lots


def test_modele_choisi_remplace_celui_de_env():
    from humatheque.extraction.fournisseurs import fournisseur
    r = Reglages(_env_file=None)
    assert fournisseur("hf", r).modele == r.hf_model
    assert fournisseur("hf#Qwen/Qwen2.5-VL-7B-Instruct", r).modele == "Qwen/Qwen2.5-VL-7B-Instruct"


@respx.mock
def test_admin_modeles_par_fournisseur(client, app):
    page = html.unescape(client.get("/admin").text)
    assert "Modèles par fournisseur" in page and app.reglages.hf_model in page and "(.env)" in page
    # hf, albert : modèles retenus par l'évaluation (config/fournisseurs.yaml), sans appel à l'API (12/11)
    options = client.get("/admin/modeles", params={"fournisseur": "hf"}).text
    assert 'value="Qwen/Qwen3.5-9B"' in options and 'value="Qwen/Qwen3.8-27B"' in options
    assert app.reglages.hf_model not in options  # déjà proposé (.env)
    options = client.get("/admin/modeles", params={"fournisseur": "albert"}).text
    assert options.count("<option") == 1 and "mistral-small-3-2-24b-instruct-2506" in options
    # vlm-local : liste de l'API du point d'accès
    app.reglages = app.reglages.model_copy(update={"vlm_api_url": "https://vlm.test/v1/chat/completions"})
    respx.get("https://vlm.test/v1/models").respond(json={"data": [{"id": "Qwen3-VL-8B"}]})
    assert 'value="Qwen3-VL-8B"' in client.get("/admin/modeles", params={"fournisseur": "vlm-local"}).text
    r = client.post("/admin/reglages", data={"fournisseur": "hf", "modele.hf": "Qwen/Qwen3.5-9B",
                                            "auteur": "Camille"})
    assert r.status_code == 303 and app.reglages_admin().modeles == {"hf": "Qwen/Qwen3.5-9B"}
    assert app.fournisseur_effectif("hf") == ("hf", "Qwen/Qwen3.5-9B")
    assert app.fournisseur_effectif("albert")[1] == app.reglages.albert_model
    # ancien nom « vlm » : alias de « vlm-local »
    assert app.fournisseur_effectif("vlm")[0] == "vlm-local"
    page = html.unescape(client.get("/admin").text)
    assert "custom-hf-endpoint" in page and 'hx-get="/admin/endpoint-hf"' in page


@respx.mock
def test_confirmer_notice_existante(client, app):
    """Arrêt Alma à reprendre, notice existant par ailleurs : le dossier passe à « terminé » (11/11)."""
    simuler(alma=sim.alma("off_profile_match_only", ppn="111111111"))
    dossier = _televerser(client).headers["location"]
    r = client.post(f"{dossier}/notice-existante", data={"commentaire": "PPN 111111111", "auteur": "Camille"})
    assert r.status_code == 303
    d = app.vue_dossier(dossier.rsplit("/", 1)[1]).dossier
    assert d.routage == "terminé" and "notice déjà présente" in d.motif_arret
    assert any(e.type == "resolution" and e.auteur == "Camille" for e in d.journal)
    page = html.unescape(client.get(dossier).text)
    assert "Confirmer : notice déjà présente" not in page
    r = client.post(f"{dossier}/notice-existante", data={"auteur": "Camille"})
    assert r.status_code == 422  # déjà terminé
