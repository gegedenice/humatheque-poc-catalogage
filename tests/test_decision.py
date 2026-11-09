"""Pipeline (SPEC §7, §11 étape 4) : un test par branche de regles/pipeline.yaml,
écrites à partir des codes renvoyés par les API (sans traduction), plus le
parcours (ordre de lecture, sinon) et l'issue d'un dossier.

Résultats : stop, stop à reprendre, conversion (notice) puis stop, poursuite,
poursuite avec alerte, poursuite à reprendre, création (notice).
"""

import pytest

from humatheque.decision.moteur import Decision, ephe, routage_final, valeurs_etape
from humatheque.regles.moteur import evaluer

TO, TR, MO = "these-originale-imprimee", "these-reproduction-imprimee", "memoire-original-imprime"
FIN, REP = "terminé", "à reprendre"


def _r(regles, etape, **valeurs):
    r = evaluer(regles, etape, valeurs)
    return (r.resultat, r.notice)


# ---------------------------------------------------------------- provenance
@pytest.mark.parametrize("valeur, attendu", [("oui", "poursuite"), ("non", "poursuite"),
                                             ("absent", "stop à reprendre"), ("ambigu", "stop à reprendre")])
def test_provenance(regles, valeur, attendu):
    assert _r(regles, "provenance", type="these", ephe=valeur)[0] == attendu


# ---------------------------------------------------------------- Alma
@pytest.mark.parametrize("status, attendu", [
    ("academic_record_found", "stop"),
    ("ambiguous_academic_candidate", "stop à reprendre"), ("off_profile_match_only", "stop à reprendre"),
    ("no_academic_record_found", "poursuite"),
    ("statut_fantome", "stop à reprendre"),  # sinon
])
def test_alma(regles, status, attendu):
    assert _r(regles, "alma", type="these", ephe="oui", status=status)[0] == attendu


# ---------------------------------------------------------------- Sudoc
CAS_SUDOC = [
    # (type, ephe, status, thesis_state) → (résultat, notice)
    (("these", "non", "duplicate_found", "printed_original"), ("conversion", TR)),
    (("these", "oui", "duplicate_found", "printed_reproduction"), ("conversion", TO)),
    (("these", "non", "duplicate_found", "microfiche_reproduction"), ("conversion", TR)),
    (("these", "oui", "duplicate_found", "microfiche_reproduction"), ("conversion", TO)),
    (("these", "oui", "duplicate_found", "unknown"), ("stop à reprendre", None)),
    (("these", "non", "electronic_only", None), ("conversion", TR)),
    (("these", "oui", "electronic_only", None), ("conversion", TO)),
    (("memoire", None, "electronic_only", None), ("conversion", MO)),
    (("memoire", None, "duplicate_found", "printed_original"), ("stop", None)),
    (("these", "oui", "ambiguous_print_candidate", None), ("stop à reprendre", None)),
    (("memoire", None, "ambiguous_print_candidate", None), ("stop à reprendre", None)),
    (("these", "oui", "no_print_duplicate_found", None), ("poursuite", None)),
    (("memoire", None, "no_print_duplicate_found", None), ("poursuite", None)),
    # non prévus (sinon) : printed_reproduction hors EPHE ; printed_original à l'EPHE (impossible)
    (("these", "non", "duplicate_found", "printed_reproduction"), ("stop à reprendre", None)),
    (("these", "oui", "duplicate_found", "printed_original"), ("stop à reprendre", None)),
]


@pytest.mark.parametrize("valeurs, attendu", CAS_SUDOC, ids=[" ".join(str(x) for x in v) for v, _ in CAS_SUDOC])
def test_sudoc(regles, valeurs, attendu):
    t, e, status, state = valeurs
    assert _r(regles, "sudoc", type=t, ephe=e, status=status, thesis_state=state) == attendu


def test_branche_sans_sous_branche_vraie_continue(regles):
    """Thèse + ambiguous_print_candidate : la branche « type = these » ne donne rien,
    la lecture continue aux branches suivantes (pas de « sinon » prématuré)."""
    r = evaluer(regles, "sudoc", {"type": "these", "ephe": "oui", "status": "ambiguous_print_candidate"})
    assert r.chemin == ["status = ambiguous_print_candidate"] and not r.sinon
    r = evaluer(regles, "sudoc", {"type": "these", "ephe": "non", "status": "duplicate_found",
                                  "thesis_state": "printed_original"})
    assert r.chemin == ["type = these", "status = duplicate_found", "thesis_state = printed_original ET ephe = non"]


# ---------------------------------------------------------------- IdRef, Dewey, création
@pytest.mark.parametrize("status, attendu", [
    ("accepted", "poursuite"), ("low_confidence", "poursuite à reprendre"),
    ("ambiguous", "poursuite à reprendre"), ("not_found", "poursuite avec alerte"),
])
def test_idref(regles, status, attendu):
    r = evaluer(regles, "idref", {"type": "these", "ephe": "oui", "status": status})
    assert r.resultat == attendu and not r.arret
    if status == "not_found":
        assert r.alerte == "non trouvé dans IdRef"


@pytest.mark.parametrize("status", ["au_dessus_seuil", "sous_seuil", "aucune_classe"])
def test_dewey_sans_verification(regles, status):
    assert _r(regles, "dewey", type="these", ephe="oui", status=status) == ("poursuite", None)


@pytest.mark.parametrize("t, e, notice", [("these", "oui", TO), ("these", "non", TR), ("memoire", None, MO)])
def test_creation(regles, t, e, notice):
    assert _r(regles, "creation", type=t, ephe=e) == ("création", notice)


# ---------------------------------------------------------------- valeurs, décision, issue
def test_valeurs_lues_sans_traduction():
    assert valeurs_etape("these", "hors EPHE", status="duplicate_found") == {
        "type": "these", "ephe": "non", "status": "duplicate_found"}
    assert (ephe("EPHE"), ephe("absent"), ephe("ambigu"), ephe("mémoire")) == ("oui", "absent", "ambigu", None)


def test_decision_depuis_le_pipeline(regles):
    r = evaluer(regles, "sudoc", {"type": "these", "ephe": "oui", "status": "electronic_only"})
    d = Decision.depuis(r, "Sudoc")
    assert (d.suite, d.profil_cible, d.routage, d.resultat) == ("convertir", TO, FIN, "conversion")
    assert d.situation == "Sudoc › type = these › status = electronic_only ET ephe = oui"
    r = evaluer(regles, "alma", {"type": "these", "ephe": "oui", "status": "off_profile_match_only"})
    d = Decision.depuis(r, "Alma")
    assert (d.suite, d.routage, d.motif) == ("arreter", REP, "Alma : candidat Alma à vérifier")


def test_ancienne_decision_relue():
    d = Decision.model_validate({"situation": "S08", "action": "création", "suite": "creer", "routage": "auto",
                                 "motif": "x", "profil_cible": TO})
    assert d.resultat == "création" and d.profil_cible == TO


@pytest.mark.parametrize("routages, final", [
    ([FIN, REP, FIN], REP),
    ([REP, "incident"], "incident"),
    ([FIN, None], FIN),
    ([], FIN),
    (["auto", "décision", "sortie", "conversion"], REP),  # ancien vocabulaire traduit
])
def test_routage_final(routages, final):
    assert routage_final(routages) == final
