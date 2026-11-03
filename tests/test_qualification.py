"""Agrégation des thesis_state (SPEC §6 étape 4, §11 étape 5)."""

import pytest

import services_simules as sim
from humatheque.qualification import agreger

SEUIL = 0.78


@pytest.mark.parametrize("candidats, statut, etat, source", [
    # Table du §11, étape 5
    ([("1", 0.9, "printed_original"), ("2", 0.85, "printed_reproduction")], "duplicate_found",
     "originale imprimée", "1"),
    ([("1", 0.9, "printed_reproduction"), ("2", 0.8, "unknown")], "duplicate_found", "indéterminé", None),
    ([("1", 0.9, "printed_original"), ("2", 0.95, "unknown")], "duplicate_found", "originale imprimée", "1"),
    ([("1", 0.95, "electronic")], "electronic_only", "électronique seule", "1"),
    ([("1", 0.7, "printed_original")], "ambiguous_print_candidate", "ambigu", None),
    # Compléments
    ([], "no_print_duplicate_found", "rien", None),
    ([("1", 0.5, "printed_original")], "no_print_duplicate_found", "rien", None),
    ([("1", 0.8, "microfiche_reproduction"), ("2", 0.99, "electronic")], "duplicate_found",
     "reproduction microfiche", "1"),
    ([("1", 0.8, "electronic"), ("2", 0.9, "electronic")], "electronic_only", "électronique seule", "2"),
    ([("1", 0.78, "printed_reproduction")], "duplicate_found", "reproduction imprimée", "1"),  # seuil atteint
])
def test_agregation(candidats, statut, etat, source):
    q = agreger(sim.sudoc(candidats, statut), SEUIL)
    assert (q.etat, q.ppn_source, q.incident) == (etat, source, False)
    assert len(q.candidats) == len(candidats)
    assert q.justification


def test_thesis_state_absent_incident():
    q = agreger(sim.sudoc([("1", 0.9, "printed_original"), ("2", 0.5, None)]), SEUIL)
    assert q.incident and q.etat is None and "thesis_state absent" in q.justification


def test_tableau_des_candidats():
    q = agreger(sim.sudoc([("1", 0.9, "printed_original"), ("2", 0.5, "electronic")]), SEUIL)
    assert [(c.ppn, c.retenu, c.etat) for c in q.candidats] == [
        ("1", True, "originale imprimée"), ("2", False, "électronique seule")]
    assert q.candidats[0].lien == "https://www.sudoc.fr/1"


def test_reponse_reelle():
    q = agreger(sim.fixture("sudoc"), SEUIL)
    assert (q.etat, q.ppn_source) == ("électronique seule", "229969437")
