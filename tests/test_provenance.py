"""Provenance (SPEC §6 étape 2, §11 étape 2)."""

import pytest

from humatheque.decision.provenance import normaliser, provenance


@pytest.mark.parametrize("etablissement, cotutelles, valeur", [
    # Table du §11, étape 2
    ("École pratique des hautes études", [], "EPHE"),
    ("EPHE", [], "EPHE"),
    ("E.P.H.E. Ve section", [], "EPHE"),
    ("Université Paris 1", [], "hors EPHE"),
    ("", [], "absent"),
    (None, [], "absent"),
    ("École des hautes études", [], "ambigu"),
    ("Université de Strasbourg", ["École pratique des hautes études"], "ambigu"),
    # Compléments
    ("ECOLE PRATIQUE DES HAUTES ETUDES, IVe section", [], "EPHE"),
    ("Université PSL - École pratique des hautes études", [], "EPHE"),
    ("École des hautes études en sciences sociales", [], "hors EPHE"),
    ("EHESS", [], "hors EPHE"),
    ("Université Paris-Sorbonne", ["EPHE"], "ambigu"),
    ("EPHE", ["Université de Genève"], "EPHE"),
    ("Stéphane Université", [], "hors EPHE"),   # « ephe » pas en mot entier
])
def test_table_provenance(etablissement, cotutelles, valeur):
    assert provenance(etablissement, cotutelles).valeur == valeur


def test_regle_et_forme_comparee_journalisees():
    p = provenance("E.P.H.E. Ve section")
    assert (p.regle, p.forme_comparee, p.forme_reconnue) == ("forme_ephe", "ephe ve section", "ephe")
    assert not p.a_confirmer
    assert provenance("").a_confirmer and provenance("École des hautes études").a_confirmer


def test_memoire():
    assert provenance("EPHE", type_document="memoire").valeur == "mémoire"


def test_normaliser():
    assert normaliser("  École  Pratique des Hautes-Études. ") == "ecole pratique des hautes etudes"
