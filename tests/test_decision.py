"""Moteur de décision (SPEC §7, §11 étape 4) : une ligne de test par règle de
regles/decisions.yaml, plus `sinon` et les routages des services.

Décision du 05/11 : une notice trouvée au-dessus du seuil (Alma présent,
candidat Sudoc retenu) arrête sans alerte (routage auto) ; seul le cas
incertain part en décision (S10, S14).
"""

import pytest

from humatheque.decision.moteur import decider, faits_du_dossier, routage_final, routage_service
from humatheque.regles.moteur import choisir

TO, TR, MO = "these-originale-imprimee", "these-reproduction-imprimee", "memoire-original-imprime"


def _faits(type_, provenance, alma, sudoc):
    return {"type": type_, "provenance": provenance, "alma": alma, "sudoc": sudoc}


# (faits) → (règle, suite, profil cible, routage)
CAS = [
    (("thèse", "EPHE", "présent", "non vérifié"), ("S01", "arreter", None, "auto")),
    (("mémoire", "sans objet", "présent", "non vérifié"), ("S01", "arreter", None, "auto")),
    (("thèse", "hors EPHE", "incertain", "non vérifié"), ("S14", "arreter", None, "décision")),
    (("thèse", "incertaine", "absent", "rien"), ("P01", "arreter", None, "décision")),
    (("thèse", "EPHE", "absent", "ambigu"), ("S10", "arreter", None, "décision")),
    (("mémoire", "sans objet", "absent", "indéterminé"), ("S10", "arreter", None, "décision")),
    (("thèse", "hors EPHE", "absent", "originale imprimée"), ("S02", "convertir", TR, "auto")),
    (("thèse", "hors EPHE", "absent", "électronique seule"), ("S03", "convertir", TR, "auto")),
    (("thèse", "hors EPHE", "absent", "reproduction imprimée"), ("S04", "arreter", None, "auto")),
    (("thèse", "hors EPHE", "absent", "reproduction microfiche"), ("S05", "convertir", TR, "auto")),
    (("thèse", "EPHE", "absent", "électronique seule"), ("S06", "convertir", TO, "auto")),
    (("thèse", "EPHE", "absent", "reproduction imprimée"), ("S07", "convertir", TO, "auto")),
    (("thèse", "EPHE", "absent", "reproduction microfiche"), ("S07", "convertir", TO, "auto")),
    (("thèse", "EPHE", "absent", "rien"), ("S08", "creer", TO, "auto")),
    (("thèse", "hors EPHE", "absent", "rien"), ("S09", "creer", TR, "auto")),
    (("mémoire", "sans objet", "absent", "rien"), ("S12", "creer", MO, "auto")),
    (("mémoire", "sans objet", "absent", "électronique seule"), ("S13", "convertir", MO, "auto")),
]


@pytest.mark.parametrize("faits, attendu", CAS)
def test_regles_de_decision(regles, faits, attendu):
    d = decider(regles, _faits(*faits))
    assert (d.situation, d.suite, d.profil_cible, d.routage) == attendu
    assert d.ligne["id"] == attendu[0] and d.faits["type"] == faits[0]


def test_toutes_les_regles_sont_testees(regles):
    assert {r.id for r in regles.decisions} == {a[0] for _, a in CAS}


def test_sinon_situation_non_prevue(regles):
    d = decider(regles, _faits("thèse", "EPHE", "absent", "originale imprimée"))
    assert (d.situation, d.suite, d.routage) == ("sinon", "arreter", "décision")
    assert d.motif.startswith("Situation non prévue") and "sudoc = originale imprimée" in d.motif


def test_motifs(regles):
    assert decider(regles, _faits("thèse", "EPHE", "présent", "non vérifié")).motif == \
        "S01 : Notice déjà présente dans Alma"
    assert decider(regles, _faits("thèse", "EPHE", "absent", "rien")).motif == "S08 : création (these-originale-imprimee)"


def test_routage_par_defaut_de_l_action(regles_fabriquees):
    """Sans champ routage : arrêt → sortie, conversion → conversion (§7)."""
    regles_fabriquees.modifier_decision("S04", routage=None)
    regles_fabriquees.modifier_decision("S06", routage=None)
    r = regles_fabriquees.charger()
    assert decider(r, _faits("thèse", "hors EPHE", "absent", "reproduction imprimée")).routage == "sortie"
    assert decider(r, _faits("thèse", "EPHE", "absent", "électronique seule")).routage == "conversion"


def test_premiere_regle_vraie(regles_fabriquees):
    regles_fabriquees.ajouter_decision({"id": "T0", "si": 'sudoc == "rien"', "alors": "décision", "motif": "test"}, 0)
    r = regles_fabriquees.charger()
    assert decider(r, _faits("thèse", "EPHE", "absent", "rien")).situation == "T0"
    assert decider(r, _faits("thèse", "EPHE", "présent", "non vérifié")).situation == "S01"


def test_evaluation_de_chaque_condition(regles):
    c = choisir(regles, _faits("thèse", "EPHE", "absent", "rien"), tout_evaluer=True)
    vraies = [e.id for e in c.evaluations if e.vraie]
    assert c.regle["id"] == "S08" and vraies == ["S08"]
    assert all(e.vraie is not None for e in c.evaluations)
    c = choisir(regles, _faits("thèse", "EPHE", "absent", "rien"))
    assert [e.vraie for e in c.evaluations][-1] is None  # après la règle retenue : non évaluée


@pytest.mark.parametrize("type_doc, provenance, attendu", [
    ("these", "EPHE", ("thèse", "EPHE")), ("these", "hors EPHE", ("thèse", "hors EPHE")),
    ("these", "absent", ("thèse", "incertaine")), ("these", "ambigu", ("thèse", "incertaine")),
    ("memoire", "mémoire", ("mémoire", "sans objet")),
])
def test_faits_du_dossier(type_doc, provenance, attendu):
    f = faits_du_dossier(type_doc, provenance, "absent", "rien")
    assert (f["type"], f["provenance"]) == attendu and (f["alma"], f["sudoc"]) == ("absent", "rien")


def test_alma_sudoc_seul_error_est_lu(regles):
    r = routage_service(regles, "sudoc-checker", "duplicate_found")
    assert r.routage is None and not r.bloquant
    r = routage_service(regles, "alma-checker", "error")
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
def test_routage_service(regles, service, statut, routage, bloquant, exception):
    r = routage_service(regles, service, statut)
    assert (r.routage, r.bloquant, r.exception) == (routage, bloquant, exception)


def test_idref_not_found_proto_notice(regles):
    r = routage_service(regles, "idref-linker /align/person", "not_found")
    assert r.alertes == ["proto-notice d'autorité à créer"] and not r.bloquant


def test_statut_inconnu_incident(regles):
    r = routage_service(regles, "dewey-classifier", "statut_fantome")
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
