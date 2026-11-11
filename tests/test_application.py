"""Couche application/ (SPEC §9.1, §11 étape 6), sans HTTP : exécuteur synchrone,
SQLite temporaire, services simulés."""

import csv
import io
import shutil

import pytest
import respx

import services_simules as sim
from humatheque.application import (
    ActionImpossible, Application, EntreeLot, ExecuteurSynchrone, Introuvable, documents_depuis_liste,
)
from humatheque.config import Reglages
from conftest import RACINE
from humatheque.store import Store
from services_simules import EXTRACTION, simuler


@pytest.fixture
def app(tmp_path):
    """Racine isolée : spec/ copiée, data/ vide."""
    shutil.copytree(RACINE / "regles", tmp_path / "regles", ignore=shutil.ignore_patterns("archives"))
    reglages = Reglages(_env_file=None).model_copy(update={"racine": tmp_path})
    return Application(store=Store(tmp_path / "data" / "t.db"), reglages=reglages,
                       fabrique=lambda s, f: sim.services(s, f), executeur=ExecuteurSynchrone())


@pytest.fixture
def image(tmp_path):
    p = tmp_path / "page.png"
    p.write_bytes(sim.png())
    return str(p)


def test_documents_depuis_liste():
    texte = "https://m/TH/a.jpg FAIT\n\n# commentaire\nhttps://m/TH/b.jpg\n"
    assert documents_depuis_liste(texte) == [["https://m/TH/a.jpg"], ["https://m/TH/b.jpg"]]


def test_accueil_etat_de_la_spec(app):
    a = app.accueil()
    assert a.spec.nb_erreurs == 0 and a.spec.nb_avertissements == 0  # cas « sinon » : informations
    assert "vlm" in a.fournisseurs and a.lots == []


def test_recharger_spec(app):
    avant = app.etat_spec()
    profil = app.reglages.dossier_regles / "notice" / "socle.yaml"
    profil.write_text(profil.read_text(encoding="utf-8").replace("variable: langue\n", "variable: langue_x\n", 1),
                      encoding="utf-8")
    apres = app.recharger_spec()
    assert apres.version.split()[1] != avant.version.split()[1]  # empreinte changée
    (erreur,) = [c for c in apres.constats if c.gravite == "erreur"]
    assert apres.nb_erreurs == 1 and "101" in erreur.ligne and "langue_x" in erreur.message
    # fichier illisible : rechargement refusé, règles en place conservées
    profil.write_text("zones: [\n", encoding="utf-8")
    with pytest.raises(ActionImpossible, match="illisibles"):
        app.recharger_spec()
    assert app.etat_spec().version == apres.version


@respx.mock
def test_lot_mode2_indicateurs_et_file(app, image):
    simuler()
    lot = app.creer_lot(EntreeLot(documents=[[image], [image], [image]], source="televersement"))
    assert lot.mode == 2
    # 2e document : notice trouvée dans Alma → sortie ; on remonte les routes après coup
    vue = app.vue_lot(lot.id)
    assert vue.indicateurs.documents == 3 and vue.indicateurs.passes_seuls == 3
    assert not vue.en_cours and vue.file == []
    assert vue.journal[0].texte.startswith("lot ouvert : 3 document(s)")
    assert vue.journal[-1].texte.startswith("lot clos : 3 terminé(s)")
    assert {e["etape"]: e["faits"] for e in vue.etapes}["generation"] == 3


@respx.mock
def test_notice_alma_certaine_hors_file(app, image):
    """Notice Alma au-dessus de match_threshold : arrêt sans alerte, hors file (05/11)."""
    simuler(alma=sim.alma("academic_record_found", ppn="229969437"))
    lot = app.creer_lot(EntreeLot(documents=[[image]]))
    vue = app.vue_lot(lot.id)
    assert vue.file == [] and vue.indicateurs.par_routage == {"terminé": 1} and vue.indicateurs.en_attente == 0
    (ligne,) = app.store.lignes_du_lot(lot.id)
    d = app.vue_dossier(ligne.id).dossier
    assert (d.decision.situation, d.etape_arret) == ("Alma › status = academic_record_found", "alma")
    assert "229969437" in d.motif_arret


@respx.mock
def test_file_d_exceptions_filtres_et_reprise(app, image):
    simuler(alma=sim.alma("ambiguous_academic_candidate", ppn="229969437"))
    lot = app.creer_lot(EntreeLot(documents=[[image], [image]]))
    vue = app.vue_lot(lot.id)
    assert vue.indicateurs.en_attente == 2 and vue.indicateurs.par_routage == {"à reprendre": 2}
    ligne = vue.file[0]
    assert (ligne.routage, ligne.etape, ligne.etape_libelle) == ("à reprendre", "alma", "Vérification Alma")
    assert "229969437" in ligne.motif
    assert app.vue_lot(lot.id, "incidents").file == []
    assert len(app.vue_lot(lot.id, "a_reprendre").file) == 2

    # Reprise : faux positif Alma sur le premier dossier
    simuler()  # Sudoc et suite répondent normalement
    app.declarer_faux_positif(ligne.dossier_id, "alma", "autre ouvrage", auteur="cat")
    vue = app.vue_lot(lot.id)
    assert vue.indicateurs.repris == 1 and vue.indicateurs.en_attente == 1
    d = app.vue_dossier(ligne.dossier_id)
    assert d.dossier.routage == "terminé" and d.dossier.notice_texte

    # Traité hors outil : sort de la file d'attente sans relance
    autre = vue.file[0].dossier_id
    with pytest.raises(ActionImpossible):
        app.traiter_hors_outil(autre, " ", auteur="cat")
    app.traiter_hors_outil(autre, "exemplarisé dans WinIBW", auteur="cat")
    vue = app.vue_lot(lot.id)
    assert vue.indicateurs.en_attente == 0 and vue.file[0].traite


@respx.mock
def test_mode1_vue_dossier_et_telechargements(app, image):
    simuler()
    lot = app.creer_lot(EntreeLot(documents=[[image]], source="televersement"))
    assert lot.mode == 1
    (ligne,) = app.store.lignes_du_lot(lot.id)
    v = app.vue_dossier(ligne.id)
    assert v.etat == "termine" and v.titre == "Contribution à l'étude de la démocratie en Afrique"
    assert [e.nom for e in v.etapes][:3] == ["extraction", "alma", "provenance"]
    assert v.etapes[0].libelle == "Extraction VLM" and v.etapes[0].statut == "ok"
    champ = next(c for c in v.champs if c.champ == "title")
    assert champ.brut == EXTRACTION["title"] and champ.normalise.startswith("Contribution à")
    assert v.telechargements == {"Notice (ISO 2709)": "notice.mrc", "Notice (UNIMARC/XML)": "notice.xml"}
    nom, contenu, mime = app.telecharger(ligne.id, "notice.xml")
    assert nom.endswith(".xml") and b"<record" in contenu and mime == "application/xml"
    with pytest.raises(Introuvable):
        app.telecharger(ligne.id, "source.mrc")
    assert v.images[0].url == ""  # image hors de data/uploads : non servie


@respx.mock
def test_conversion_telechargements_iso(app, image):  # ALM-SUD-F-002
    simuler(sudoc=sim.sudoc([("229969437", 0.95, "electronic")], "electronic_only"))
    lot = app.creer_lot(EntreeLot(documents=[[image]]))
    (ligne,) = app.store.lignes_du_lot(lot.id)
    v = app.vue_dossier(ligne.id)
    assert v.dossier.routage == "terminé"  # candidat Sudoc retenu : conversion sans alerte (05/11)
    assert set(v.telechargements.values()) == {"notice.mrc", "notice.xml", "source.mrc", "source.xml"}
    _, iso, _ = app.telecharger(ligne.id, "source.mrc")
    assert iso.endswith(b"\x1d")
    _, xml, _ = app.telecharger(ligne.id, "source.xml")
    assert b'tag="029"' in xml and b"2017EHES0033" in xml
    assert "2017EHES0033" in app.export_notices(lot.id).decode()


@respx.mock
def test_resolutions_idref_et_dewey(app, image):
    simuler(idref_statut="low_confidence")
    lot = app.creer_lot(EntreeLot(documents=[[image]]))
    (ligne,) = app.store.lignes_du_lot(lot.id)
    v = app.vue_dossier(ligne.id)
    assert v.dossier.routage == "à reprendre" and v.exceptions_ouvertes == 3
    i = next(i for i, e in enumerate(v.dossier.exceptions) if e.objet.startswith("author"))
    with pytest.raises(ActionImpossible, match="mal formé"):
        app.choisir_ppn(ligne.id, i, "123", auteur="cat")
    v = app.choisir_ppn(ligne.id, i, "026940345", auteur="cat")
    assert "700 #1$3026940345$4070" in v.dossier.notice_texte.splitlines()
    assert v.exceptions_ouvertes == 2
    # « sans lien » pour les deux autres → plus d'exception, routage auto
    for j, e in enumerate(v.dossier.exceptions):
        if not e.resolue:
            v = app.choisir_ppn(ligne.id, j, None, auteur="cat")
    assert v.exceptions_ouvertes == 0 and v.dossier.routage == "terminé"
    assert "701 #1$aPetit$bLuc$4958$4555" in v.dossier.notice_texte.splitlines()


@respx.mock
def test_resolutions_idref_groupees_tout_ou_rien(app, image):
    """Validation groupée des choix IdRef : un PPN invalide n'applique rien ; sinon une seule relance."""
    simuler(idref_statut="low_confidence")
    lot = app.creer_lot(EntreeLot(documents=[[image]]))
    (ligne,) = app.store.lignes_du_lot(lot.id)
    v = app.vue_dossier(ligne.id)
    idref = [i for i, e in enumerate(v.dossier.exceptions) if e.etape.startswith("idref")]
    auteur_i = next(i for i in idref if v.dossier.exceptions[i].objet.startswith("author"))
    autres = [i for i in idref if i != auteur_i]
    with pytest.raises(ActionImpossible, match="mal formé"):
        app.choisir_ppns(ligne.id, {auteur_i: "026940345", autres[0]: "123"}, auteur="cat")
    assert app.vue_dossier(ligne.id).exceptions_ouvertes == v.exceptions_ouvertes  # rien appliqué
    with pytest.raises(ActionImpossible, match="aucun choix"):
        app.choisir_ppns(ligne.id, {}, auteur="cat")
    v = app.choisir_ppns(ligne.id, {auteur_i: "026940345", **{j: None for j in autres}}, auteur="cat")
    assert v.exceptions_ouvertes == 0 and v.dossier.routage == "terminé"
    assert "700 #1$3026940345$4070" in v.dossier.notice_texte.splitlines()
    resolutions = [e for e in v.dossier.journal if e.type == "resolution"]
    assert len(resolutions) == len(idref) and {e.auteur for e in resolutions} == {"cat"}


@respx.mock
def test_resolution_dewey(app, image):
    simuler()
    d = app.vue_dossier(app.store.lignes_du_lot(app.creer_lot(EntreeLot(documents=[[image]])).id)[0].id).dossier
    # Seuil élevé : la classe devient une exception
    from humatheque.services.dewey import ResultatDewey
    d.dewey = ResultatDewey(statut="sous_seuil", classes=d.dewey.classes)
    from humatheque.workflow.modeles import ExceptionDossier
    d.exceptions.append(ExceptionDossier(etape="dewey", objet="classe Dewey", motif="Dewey : sous_seuil"))
    ligne = app.store.ligne_dossier(d.id)
    app.store.enregistrer_dossier(d, ligne.lot_id, ligne.rang, "termine")
    with pytest.raises(ActionImpossible):
        app.choisir_dewey(d.id, len(d.exceptions) - 1, "123", auteur="cat")
    v = app.choisir_dewey(d.id, len(d.exceptions) - 1, "700", auteur="cat")
    assert "686 ##$a700$2TEF" in v.dossier.notice_texte.splitlines()


@respx.mock
def test_provenance_puis_relance(app, image):
    routes = simuler(extraction=EXTRACTION | {"granting_institution": "École des hautes études"})
    lot = app.creer_lot(EntreeLot(documents=[[image]]))
    (ligne,) = app.store.lignes_du_lot(lot.id)
    d = app.vue_dossier(ligne.id).dossier  # provenance ambiguë (après Alma) : STOP – à reprendre avant Sudoc
    assert (d.etape_arret, d.routage) == ("provenance", "à reprendre") and routes["alma"].call_count == 1
    assert not routes["sudoc"].called
    with pytest.raises(ActionImpossible):
        app.choisir_provenance(ligne.id, "peut-être", auteur="cat")
    v = app.choisir_provenance(ligne.id, "hors EPHE", auteur="cat")
    assert routes["alma"].call_count == 1  # la reprise part de la provenance : Alma n'est pas rappelé
    assert v.dossier.decision.situation == "Création de la notice › type = these ET ephe = non"
    assert v.dossier.notice_texte and v.dossier.decision.profil_cible == "these-reproduction-imprimee"
    app.relancer(ligne.id, depuis="extraction")
    assert routes["vlm"].call_count == 2
    with pytest.raises(ActionImpossible):
        app.relancer(ligne.id, depuis="nulle_part")


def test_erreur_inattendue_dossier_en_incident(app, image):
    def casse(spec, fournisseur):
        raise ValueError("URL du service alma-checker non configurée")
    app.fabrique = casse
    lot = app.creer_lot(EntreeLot(documents=[[image]]))
    d = app.vue_dossier(app.store.lignes_du_lot(lot.id)[0].id)
    assert d.etat == "termine" and d.dossier.routage == "incident"
    assert "non configurée" in d.dossier.motif_arret


@respx.mock
def test_exports_et_historique(app, image):
    simuler()
    lot = app.creer_lot(EntreeLot(documents=[[image], [image]], identifiants=["TH/TH-01", "TH/TH-02"],
                                  source="minio"))
    lignes = list(csv.DictReader(io.StringIO(app.export_csv(lot.id))))
    assert [l["document"] for l in lignes] == ["TH/TH-01", "TH/TH-02"]
    assert lignes[0]["issue"] == "terminé" and lignes[0]["regle_de_decision"] == "Création de la notice › type = these ET ephe = oui" and lignes[0]["version_spec"]
    notices = app.export_notices(lot.id)
    assert notices.count(b"\x1d") == 2  # deux notices ISO 2709 à la suite
    assert app.export_notices(lot.id, "xml").count(b"<record") == 2
    h = app.historique()
    assert h.ingestions[0].lot_id == lot.id and h.ingestions[0].documents == ["TH/TH-01", "TH/TH-02"]
    assert h.lots[0].lot.id == lot.id
    with pytest.raises(Introuvable):
        app.export_csv("L-inconnu")


def test_televersement(app):
    docs = app.enregistrer_televersement([("p1.png", sim.png()), ("../p 2.png", sim.png())], un_seul_document=True)
    assert len(docs) == 1 and len(docs[0]) == 2
    assert docs[0][1].endswith("002_p_2.png")
    assert app.url_image(docs[0][0]).startswith("/fichiers/")
    rel = app.url_image(docs[0][0]).removeprefix("/fichiers/")
    assert app.chemin_fichier(rel).read_bytes().startswith(b"\x89PNG")
    with pytest.raises(Introuvable):
        app.chemin_fichier("../t.db")
    assert len(app.enregistrer_televersement([("a.png", b"x"), ("b.png", b"y")], un_seul_document=False)) == 2


def test_lot_vide_ou_fournisseur_inconnu(app):
    with pytest.raises(ActionImpossible):
        app.creer_lot(EntreeLot(documents=[]))
    with pytest.raises(ActionImpossible):
        app.creer_lot(EntreeLot(documents=[["x"]], fournisseur="inconnu"))


# ---------------------------------------------------------------- notice éditable (étape 6 ter)
@respx.mock
def test_notice_versions_validation_et_regeneration(app, image):
    simuler(idref_statut="low_confidence")
    lot = app.creer_lot(EntreeLot(documents=[[image]]))
    (ligne,) = app.store.lignes_du_lot(lot.id)
    n = app.vue_notice(ligne.id)
    assert (n.version, n.statut, n.source, n.profil) == (1, "générée", "générée", "these-originale-imprimee")
    assert n.marqueurs == []  # repli $a$b : pas de PPN

    # validation refusée tant qu'il reste une erreur
    casse = n.texte.replace("101 0#$afre", "101 0#$a")
    assert [m.message for m in app.valider_texte(ligne.id, casse)] == ["sous-zone $a sans valeur"]
    n2 = app.enregistrer_notice(ligne.id, casse, "cat", base=1)
    assert (n2.version, n2.statut) == (2, "éditée")
    with pytest.raises(ActionImpossible) as e:
        app.valider_notice(ligne.id, "cat")
    assert e.value.details and e.value.details[0].gravite == "erreur"
    from humatheque.application import Conflit
    with pytest.raises(Conflit):
        app.enregistrer_notice(ligne.id, n.texte, "cat", base=1)

    corrigee = casse.replace("101 0#$a\n", "101 0#$afre\n") + "\n610 ##$aDémocratie"
    app.enregistrer_notice(ligne.id, corrigee, "cat", base=2)
    n4 = app.valider_notice(ligne.id, "cat")
    assert (n4.version, n4.statut) == (4, "validée")
    _, contenu, _ = app.telecharger(ligne.id, "notice.xml")
    assert "Démocratie" in contenu.decode()  # export = version validée
    assert "Démocratie" in app.export_notices(lot.id).decode()

    # résolution IdRef → régénération proposée, l'édition n'est pas écrasée
    i = next(i for i, e in enumerate(app.vue_dossier(ligne.id).dossier.exceptions) if e.objet.startswith("author"))
    app.choisir_ppn(ligne.id, i, "026940345", auteur="cat")
    n5 = app.vue_notice(ligne.id)
    assert n5.version == 4 and n5.texte == corrigee
    assert n5.proposition and "+700 #1$3026940345$4070" in n5.proposition.diff
    n6 = app.reporter_proposition(ligne.id, n5.proposition.numero, "cat")
    assert n6.statut == "éditée" and "700 #1$3026940345$4070" in n6.texte and n6.proposition is None
    with pytest.raises(ActionImpossible):
        app.ignorer_proposition(ligne.id, n5.proposition.numero, "cat")


@respx.mock
def test_notice_convertie_editable(app, image):
    simuler(sudoc=sim.sudoc([("229969437", 0.95, "electronic")], "electronic_only"))
    lot = app.creer_lot(EntreeLot(documents=[[image]]))
    (ligne,) = app.store.lignes_du_lot(lot.id)
    n = app.vue_notice(ligne.id)
    assert (n.version, n.source, n.profil) == (1, "convertie", "these-originale-imprimee")
    assert "001 229969437" in n.texte


def test_identifiant_document_par_url():
    ident = Application._identifiant_document
    assert ident(["https://minio.smartbiblia.fr/images/memoires/CESOR/doc_01/page_de_titre.jpg?x=1"]) == \
        "images/memoires/CESOR/doc_01/page_de_titre.jpg"
    assert ident(["/data/uploads/ab/001_p.png"]) == "001_p.png"


@pytest.mark.parametrize("identifiant, nom", [
    ("p0.png", "p0"), ("TH/Thèse-01/page_de_titre.JPG", "TH_These-01_page_de_titre"),
    ("https-chemin/a b.tiff", "https-chemin_a_b"), ("///", "notice"),
])
def test_nom_de_fichier_sans_extension_d_image(identifiant, nom):
    from humatheque.application import nom_de_fichier
    assert nom_de_fichier(identifiant) == nom
