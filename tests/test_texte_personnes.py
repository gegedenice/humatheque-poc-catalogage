"""Découpage des personnes (SPEC §4.3)."""

import pytest

from humatheque.texte_unimarc import personne


# Table du §4.3
@pytest.mark.parametrize("brut, nom, prenom, alertes", [
    ("Jean-Daniel DUBOIS", "Dubois", "Jean-Daniel", []),
    ("M. Jean-Daniel DUBOIS", "Dubois", "Jean-Daniel", []),
    ("JEAN-DANIEL DUBOIS", "Dubois", "Jean-Daniel", ["decoupage_heuristique"]),
    ("Pr Marie-France de LA ROCHE", "La Roche", "Marie-France de", ["particule_a_verifier"]),
])
def test_table_spec(brut, nom, prenom, alertes):
    p = personne(brut)
    assert (p.nom, p.prenom, p.alertes) == (nom, prenom, alertes)


@pytest.mark.parametrize("brut, nom, prenom", [
    ("DUBOIS Jean-Daniel", "Dubois", "Jean-Daniel"),          # nom en tête
    ("Jean D'ORMESSON", "Ormesson", "Jean d'"),               # particule élidée
    ("JEAN DE LA FONTAINE", "La Fontaine", "Jean de"),        # tout en capitales + particules
    ("Joachim du BELLAY", "Du Bellay", "Joachim"),            # article conservé en tête du nom
    ("Madame Marie Curie", "Curie", "Marie"),                 # tout en casse mixte
    ("J.-D. DUBOIS", "Dubois", "J.-D."),                      # initiales
    ("Docteur Anne-Marie O'BRIEN", "O'Brien", "Anne-Marie"),
    # Essai réel du 04/11
    ("Monsieur le Professeur André GODIN", "Godin", "André"),
    ("Madame la Professeure Anne LEROY", "Leroy", "Anne"),
    ("le Professeur Jean MARTIN", "Martin", "Jean"),
])
def test_decoupages(brut, nom, prenom):
    p = personne(brut)
    assert (p.nom, p.prenom) == (nom, prenom)


def test_forme_sans_civilite_casse_d_origine():
    assert personne("M. Jean-Daniel DUBOIS").forme == "Jean-Daniel DUBOIS"
    assert personne("Monsieur le Professeur André GODIN").forme == "André GODIN"


def test_article_non_civilite_conserve():
    # « La » n'est retiré que suivi d'une civilité
    assert personne("LA FONTAINE Jean").nom == "La Fontaine"


def test_pas_de_reaccentuation_mais_alerte_si_capitales():
    p = personne("HELENE MARTIN")
    assert p.prenom == "Helene"
    assert "accent_possible:HELENE" in p.alertes
    assert not any(a.startswith("accent_possible") for a in personne("Helene MARTIN").alertes)


def test_personne_vide():
    assert personne("  ").alertes == ["personne_vide"]
    assert personne("Monsieur").nom == ""
