"""Réaccentuation et casse (SPEC §4.1, §4.2), avec le dictionnaire de reaccentue."""

import pytest

from humatheque.texte_unimarc import casse_complement_titre, casse_phrase, reaccentuer


# Cas d'acceptation minimaux du §4.1 (sans alerte attendue)
@pytest.mark.parametrize("entree, sortie", [
    ("CONTRIBUTION A L'ETUDE DE LA DEMOCRATIE EN AFRIQUE",
     "Contribution à l'étude de la démocratie en Afrique"),
    ("ECOLE PRATIQUE DES HAUTES ETUDES", "École pratique des hautes études"),
    ("ETUDE DE L'ETE 1968", "Étude de l'été 1968"),
    ("HISTOIRE DES ETATS-UNIS", "Histoire des États-Unis"),
    ("Les paysans de Normandie", "Les paysans de Normandie"),
])
def test_cas_acceptation(entree, sortie):
    r = casse_phrase(entree)
    assert r.valeur == sortie
    assert r.alertes == []


def test_casse_mixte_inchangee_sans_trace():
    r = casse_phrase("Les Paysans De Normandie")
    assert r.valeur == "Les Paysans De Normandie" and r.trace == []


def test_seuls_les_segments_en_capitales_sont_traites():
    assert casse_phrase("Les paysans : ETUDE DE L'ETE").valeur == "Les paysans : étude de l'été"


def test_apostrophe_typographique_conservee():
    assert casse_phrase("ETUDE DE L’ETE").valeur == "Étude de l’été"


def test_accent_ambigu_sans_mot_precedent():
    r = reaccentuer("DES")  # dés / des / dès, aucun mot précédent
    assert r.valeur == "des"
    assert r.alertes == ["accent_ambigu:DES"]


def test_bigramme_tranche_et_trace():
    r = casse_phrase("CONTRIBUTION A L'ETUDE")
    assert any("bigramme « contribution à »" in t for t in r.trace)


def test_nom_propre_seulement_en_majuscule():
    assert casse_phrase("LA FRANCE ET L'AFRIQUE").valeur == "La France et l'Afrique"


def test_nom_propre_possible_passe_en_minuscule_avec_alerte():
    r = casse_phrase("LA PENSEE DE ROUSSEAU")
    assert r.valeur == "La pensée de rousseau"
    assert "nom_propre_possible:ROUSSEAU" in r.alertes


def test_nom_propre_connu_du_document_garde_sa_majuscule():
    r = casse_phrase("LA PENSEE DE ROUSSEAU", noms_connus=["Jean-Jacques ROUSSEAU"])
    assert r.valeur == "La pensée de Rousseau"
    assert not any(a.startswith("nom_propre_possible") for a in r.alertes)


def test_mot_inconnu_alerte():
    r = casse_phrase("ETUDE DU ZORGLUBISME")
    assert r.valeur == "Étude du zorglubisme"
    assert "mot_inconnu:ZORGLUBISME" in r.alertes


def test_chiffres_romains_conserves():
    r = casse_phrase("LA FRANCE AU XIXE SIECLE")
    assert r.valeur == "La France au XIXe siècle"


@pytest.mark.parametrize("entree, sortie", [
    ("LES ENJEUX MEDICAUX, CULTURELS ET SOCIAUX", "les enjeux médicaux, culturels et sociaux"),
    ("AFRIQUE ET EUROPE", "Afrique et Europe"),        # nom propre en tête : majuscule gardée
    ("étude de cas", "étude de cas"),                  # casse mixte : inchangé
])
def test_casse_complement_titre(entree, sortie):
    assert casse_complement_titre(entree).valeur == sortie


def test_vide():
    assert casse_phrase("").valeur == "" and casse_phrase(None).valeur == ""
