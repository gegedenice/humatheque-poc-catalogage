"""Découpage nom / prénom (SPEC §4.3). Décision du 11/11 : la casse, les accents et
les civilités sont faits par le VLM ; ici, découpage seulement, sans changer
l'écriture des mots (civilités résiduelles retirées)."""

import pytest

from humatheque.texte_unimarc import personne


@pytest.mark.parametrize("brut, nom, prenom", [
    # sortie attendue du VLM : casse de phrase, civilités retirées
    ("Jean-Daniel Dubois", "Dubois", "Jean-Daniel"),
    ("Marie-France de La Roche", "La Roche", "Marie-France de"),
    ("Jean de La Fontaine", "La Fontaine", "Jean de"),
    ("Joachim du Bellay", "du Bellay", "Joachim"),
    ("Jean d'Ormesson", "Ormesson", "Jean d'"),
    ("J.-D. Dubois", "Dubois", "J.-D."),
    ("Anne-Marie O'Brien", "O'Brien", "Anne-Marie"),
    # capitales laissées par le modèle : le bloc en capitales est le nom, sans recasse
    ("Jean-Daniel DUBOIS", "DUBOIS", "Jean-Daniel"),
    ("DUBOIS Jean-Daniel", "DUBOIS", "Jean-Daniel"),
    # civilités résiduelles
    ("M. Jean-Daniel Dubois", "Dubois", "Jean-Daniel"),
    ("Monsieur le Professeur André Godin", "Godin", "André"),
    ("Madame la Professeure Anne Leroy", "Leroy", "Anne"),
])
def test_decoupage(brut, nom, prenom):
    p = personne(brut)
    assert (p.nom, p.prenom) == (nom, prenom)
    assert p.alertes == []  # plus d'alertes de découpage ni d'accents


def test_forme_sans_civilite_ni_recasse():
    p = personne("Pr Marie-France de La Roche")
    assert p.forme == "Marie-France de La Roche"
    assert personne("Ivar EKELAND").forme == "Ivar EKELAND"  # l'écriture est celle du VLM


def test_article_non_civilite_conserve():
    assert personne("Le Goff Jacques").forme == "Le Goff Jacques"


def test_personne_vide():
    p = personne("Monsieur")
    assert (p.nom, p.prenom, p.forme) == ("", "", "")
