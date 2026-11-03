"""Réaccentuation et casse (SPEC §4.1, §4.2), avec le dictionnaire de reaccentue."""

import pytest

from humatheque.texte_unimarc import casse_complement_titre, casse_phrase, reaccentuer
from humatheque.texte_unimarc._vendor.reaccentue.dictionnaire import Dictionnaire


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


def _dico(**champs):
    base = dict(formes={}, bigrammes={}, noms_propres={}, noms_propres_ambigus=set(),
                mots_connus=set(), frequences={})
    base.update(champs)
    return Dictionnaire(**base)


@pytest.mark.parametrize("entree, sortie", [("DES", "des"), ("LA", "la"), ("LES", "les"), ("DE", "de")])
def test_sans_mot_precedent_frequence_totale(entree, sortie):
    r = reaccentuer(entree)
    assert r.valeur == sortie and r.alertes == []
    assert "fréquence totale" in r.trace[0]


def test_egalite_ou_absence_de_donnees_accent_ambigu():
    d = _dico(formes={"COTE": ["côte", "coté", "cote"]}, mots_connus={"COTE"})
    r = reaccentuer("COTE", d=d)
    assert r.valeur == "cote" and r.alertes == ["accent_ambigu:COTE"]
    d = _dico(formes={"COTE": ["côte", "coté"]}, mots_connus={"COTE"}, frequences={"côte": 5, "coté": 5})
    assert reaccentuer("COTE", d=d).alertes == ["accent_ambigu:COTE"]


def test_bigramme_prioritaire_sur_frequence_totale():
    d = _dico(formes={"COTE": ["côte", "coté"], "LA": ["la"]}, mots_connus={"COTE", "LA"},
              bigrammes={"la côte": 3}, frequences={"coté": 100, "côte": 3})
    assert reaccentuer("LA COTE", d=d).valeur == "la côte"


def test_noms_propres_courts_ignores():
    r = casse_phrase("QU'EST-CE QUE LA DEMOCRATIE ?")
    assert r.valeur == "Qu'est-ce que la démocratie ?"
    assert not any(a.startswith("nom_propre_possible") for a in r.alertes)


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
