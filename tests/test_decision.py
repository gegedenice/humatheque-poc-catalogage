"""Moteur de décision (SPEC §7, §11 étape 4) : une ligne par situation S01 à S13."""

import pytest

from humatheque.decision.moteur import Cle, decider, routage_final, routage_service
from humatheque.spec import charger_spec

TO, TR, MO = "these_originale_imprimee", "these_reproduction_imprimee", "memoire_original_imprime"


@pytest.fixture(scope="module")
def spec():
    return charger_spec(source="csv")


def _d(spec, alma, sudoc, prov):
    return decider(spec, Cle(etat_alma=alma, etat_sudoc=sudoc, provenance=prov))


# (clé) → (situation, suite, profil cible, routage)
@pytest.mark.parametrize("cle, attendu", [
    (("présent", "rien", "EPHE"), ("S01", "arreter", None, "sortie")),
    (("présent", "originale imprimée", "hors EPHE"), ("S01", "arreter", None, "sortie")),
    (("présent", "indéterminé", "mémoire"), ("S01", "arreter", None, "sortie")),
    (("absent", "originale imprimée", "hors EPHE"), ("S02", "convertir", TR, "conversion")),
    (("absent", "électronique seule", "hors EPHE"), ("S03", "convertir", TR, "conversion")),
    (("absent", "reproduction imprimée", "hors EPHE"), ("S04", "arreter", None, "sortie")),
    (("absent", "reproduction microfiche", "hors EPHE"), ("S05", "convertir", TR, "conversion")),
    (("absent", "électronique seule", "EPHE"), ("S06", "convertir", TO, "conversion")),
    (("absent", "reproduction imprimée", "EPHE"), ("S07", "convertir", TO, "conversion")),
    (("absent", "reproduction microfiche", "EPHE"), ("S07", "convertir", TO, "conversion")),
    (("absent", "rien", "EPHE"), ("S08", "creer", TO, "auto")),
    (("absent", "rien", "hors EPHE"), ("S09", "creer", TR, "auto")),
    (("absent", "ambigu", "EPHE"), ("S10", "arreter", None, "décision")),
    (("absent", "ambigu", "mémoire"), ("S10", "arreter", None, "décision")),
    (("absent", "rien", "mémoire"), ("S12", "creer", MO, "auto")),
    (("absent", "électronique seule", "mémoire"), ("S13", "convertir", MO, "conversion")),
])
def test_situations(spec, cle, attendu):
    d = _d(spec, *cle)
    assert (d.situation, d.suite, d.profil_cible, d.routage) == attendu
    assert d.ligne["id"] == d.situation


@pytest.mark.parametrize("cle", [
    ("absent", "originale imprimée", "EPHE"),
    ("absent", "originale imprimée", "mémoire"),
    ("absent", "reproduction imprimée", "mémoire"),
    ("absent", "indéterminé", "EPHE"),
])
def test_situation_non_prevue(spec, cle):
    d = _d(spec, *cle)
    assert (d.situation, d.suite, d.routage) == (None, "arreter", "décision")
    assert d.motif == f"situation non prévue ({', '.join(cle)})"


def test_deriver_conversion_puis_arret_quel_que_soit_le_routage_de_la_table(spec):
    d = _d(spec, "absent", "originale imprimée", "hors EPHE")
    assert d.ligne["routage"] == "sortie"            # S02 : routage de la table
    assert (d.suite, d.routage) == ("convertir", "conversion")
    assert d.action.startswith("Dériver")


def test_premiere_ligne_qui_correspond(spec_fabriquee):
    spec_fabriquee.ajouter("Situations", id="S99", etat_alma="absent", etat_sudoc="rien",
                           provenance="*", action="Arrêt", routage="sortie")
    spec = charger_spec(source="csv", dossier=spec_fabriquee.dossier)
    assert _d(spec, "absent", "rien", "EPHE").situation == "S08"
    assert _d(spec, "absent", "rien", "inconnue").situation == "S99"


def test_alias_etat_sudoc(spec_fabriquee):
    spec_fabriquee.modifier("Situations", "id", "S02", etat_sudoc="originale imprimée seule")
    spec = charger_spec(source="csv", dossier=spec_fabriquee.dossier)
    assert _d(spec, "absent", "originale imprimée", "hors EPHE").situation == "S02"


# --- Statuts de service
def test_alma_sudoc_seul_error_est_lu(spec):
    r = routage_service(spec, "sudoc-checker", "duplicate_found")
    assert r.routage is None and not r.bloquant
    r = routage_service(spec, "alma-checker", "error")
    assert (r.routage, r.bloquant) == ("incident", True)


@pytest.mark.parametrize("service, statut, routage, bloquant, exception", [
    ("idref-linker /align/person", "accepted", "auto", False, False),
    ("idref-linker /align/person", "ambiguous", "décision", False, True),
    ("idref-linker /align/person", "error", "incident", True, False),
    ("idref-linker /align/thesis-organizations", "low_confidence", "décision", False, True),
    ("dewey-classifier", "sous_seuil", "décision", False, True),
    ("extraction-vlm", "json_invalide", "incident", True, False),
    ("extraction-vlm", "confiance_basse", "décision", True, True),
])
def test_routage_service(spec, service, statut, routage, bloquant, exception):
    r = routage_service(spec, service, statut)
    assert (r.routage, r.bloquant, r.exception) == (routage, bloquant, exception)


def test_idref_not_found_proto_notice(spec):
    r = routage_service(spec, "idref-linker /align/person", "not_found")
    assert r.alertes == ["proto-notice d'autorité à créer"] and not r.bloquant


def test_statut_inconnu_incident(spec):
    r = routage_service(spec, "dewey-classifier", "statut_fantome")
    assert (r.routage, r.bloquant) == ("incident", True)


@pytest.mark.parametrize("routages, final", [
    (["auto", "décision", "auto"], "décision"),
    (["auto", "conversion", "décision"], "conversion"),
    (["décision", "sortie", "conversion"], "sortie"),
    (["sortie", "incident"], "incident"),
    (["auto", None], "auto"),
    ([], "auto"),
])
def test_routage_final(routages, final):
    assert routage_final(routages) == final


def test_routage_final_inconnu():
    with pytest.raises(ValueError):
        routage_final(["auto", "peut-être"])
