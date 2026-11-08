"""Moteur de décision (SPEC §7, §11 étape 4) : un test par règle de
regles/decisions.yaml, plus `sinon`, le dictionnaire des faits (codes d'API →
valeurs), l'issue des réponses de services (regles/services.yaml : IdRef
personnes et structures, Dewey, extraction) et l'issue d'un dossier.

Issues : terminé (rien à faire), à reprendre (un catalogueur tranche),
incident (panne technique). Décision du 05/11 : une notice trouvée au-dessus
du seuil arrête sans alerte (terminé) ; seul le cas incertain est à reprendre.
"""

import pytest

from humatheque.decision.moteur import decider, faits_du_dossier, routage_final, routage_service
from humatheque.regles.moteur import choisir

TO, TR, MO = "these-originale-imprimee", "these-reproduction-imprimee", "memoire-original-imprime"
FIN, REP = "terminé", "à reprendre"


def _faits(type_, provenance, alma, sudoc):
    return {"type": type_, "provenance": provenance, "alma": alma, "sudoc": sudoc}


# (faits) → (règle, suite, profil cible, issue)
CAS = [
    (("thèse", "EPHE", "présent", "non vérifié"), ("Notice déjà dans Alma", "arreter", None, FIN)),
    (("mémoire", "sans objet", "présent", "non vérifié"), ("Notice déjà dans Alma", "arreter", None, FIN)),
    (("thèse", "hors EPHE", "incertain", "non vérifié"), ("Candidat Alma à vérifier", "arreter", None, REP)),
    (("thèse", "incertaine", "absent", "rien"), ("Provenance à confirmer", "arreter", None, REP)),
    (("thèse", "EPHE", "absent", "ambigu"), ("Résultat Sudoc à vérifier", "arreter", None, REP)),
    (("mémoire", "sans objet", "absent", "indéterminé"), ("Résultat Sudoc à vérifier", "arreter", None, REP)),
    (("thèse", "hors EPHE", "absent", "originale imprimée"),
     ("Thèse hors EPHE, originale imprimée au Sudoc", "convertir", TR, FIN)),
    (("thèse", "hors EPHE", "absent", "électronique seule"),
     ("Thèse hors EPHE, notice électronique au Sudoc", "convertir", TR, FIN)),
    (("thèse", "hors EPHE", "absent", "reproduction imprimée"),
     ("Thèse hors EPHE, reproduction imprimée déjà au Sudoc", "arreter", None, FIN)),
    (("thèse", "hors EPHE", "absent", "reproduction microfiche"),
     ("Thèse hors EPHE, reproduction microfiche au Sudoc", "convertir", TR, FIN)),
    (("thèse", "EPHE", "absent", "électronique seule"), ("Thèse EPHE, notice électronique au Sudoc", "convertir", TO, FIN)),
    (("thèse", "EPHE", "absent", "reproduction imprimée"), ("Thèse EPHE, reproduction au Sudoc", "convertir", TO, FIN)),
    (("thèse", "EPHE", "absent", "reproduction microfiche"), ("Thèse EPHE, reproduction au Sudoc", "convertir", TO, FIN)),
    (("thèse", "EPHE", "absent", "rien"), ("Thèse EPHE absente du Sudoc", "creer", TO, FIN)),
    (("thèse", "hors EPHE", "absent", "rien"), ("Thèse hors EPHE absente du Sudoc", "creer", TR, FIN)),
    (("mémoire", "sans objet", "absent", "rien"), ("Mémoire absent du Sudoc", "creer", MO, FIN)),
    (("mémoire", "sans objet", "absent", "électronique seule"), ("Mémoire, notice électronique au Sudoc", "convertir", MO, FIN)),
]


@pytest.mark.parametrize("faits, attendu", CAS)
def test_regles_de_decision(regles, faits, attendu):
    d = decider(regles, _faits(*faits))
    assert (d.situation, d.suite, d.profil_cible, d.routage) == attendu
    assert d.ligne["nom"] == attendu[0] and d.faits["type"] == faits[0]


def test_toutes_les_regles_sont_testees(regles):
    assert {r.nom for r in regles.decisions} == {a[0] for _, a in CAS}


def test_sinon_situation_non_prevue(regles):
    d = decider(regles, _faits("thèse", "EPHE", "absent", "originale imprimée"))
    assert (d.situation, d.suite, d.routage) == ("sinon", "arreter", REP)
    assert d.motif.startswith("Situation non prévue") and "sudoc = originale imprimée" in d.motif


def test_motif_et_regle_a_valider(regles):
    d = decider(regles, _faits("thèse", "incertaine", "absent", "rien"))
    assert d.motif == "Provenance à confirmer : établissement de soutenance absent ou ambigu"
    assert d.ligne.get("à_valider") is None
    d = decider(regles, _faits("thèse", "hors EPHE", "incertain", "non vérifié"))
    assert d.motif == "Candidat Alma à vérifier" and d.ligne["à_valider"] is True


def test_premiere_regle_vraie(regles_fabriquees):
    regles_fabriquees.ajouter_decision({"nom": "Test", "si": 'sudoc == "rien"', "alors": "vérification"}, 0)
    r = regles_fabriquees.charger()
    assert decider(r, _faits("thèse", "EPHE", "absent", "rien")).situation == "Test"
    assert decider(r, _faits("thèse", "EPHE", "présent", "non vérifié")).situation == "Notice déjà dans Alma"


def test_evaluation_de_chaque_condition(regles):
    c = choisir(regles, _faits("thèse", "EPHE", "absent", "rien"), tout_evaluer=True)
    vraies = [e.nom for e in c.evaluations if e.vraie]
    assert c.regle["nom"] == "Thèse EPHE absente du Sudoc" and vraies == ["Thèse EPHE absente du Sudoc"]
    assert all(e.vraie is not None for e in c.evaluations)
    c = choisir(regles, _faits("thèse", "EPHE", "absent", "rien"))
    assert [e.vraie for e in c.evaluations][-1] is None  # après la règle retenue : non évaluée


@pytest.mark.parametrize("type_doc, provenance, attendu", [
    ("these", "EPHE", ("thèse", "EPHE")), ("these", "hors EPHE", ("thèse", "hors EPHE")),
    ("these", "absent", ("thèse", "incertaine")), ("these", "ambigu", ("thèse", "incertaine")),
    ("memoire", "mémoire", ("mémoire", "sans objet")),
])
def test_faits_du_dossier(regles, type_doc, provenance, attendu):
    f = faits_du_dossier(regles, type_doc, provenance, "absent", "rien")
    assert (f["type"], f["provenance"]) == attendu and (f["alma"], f["sudoc"]) == ("absent", "rien")


# ---------------------------------------------------------------- dictionnaire des faits
@pytest.mark.parametrize("fait, code, valeur", [
    ("alma", "academic_record_found", "présent"), ("alma", "ambiguous_academic_candidate", "incertain"),
    ("alma", "off_profile_match_only", "incertain"), ("alma", "no_academic_record_found", "absent"),
    ("alma", "statut_fantome", None),  # pas de code_inconnu : incident
    ("sudoc", "printed_original", "originale imprimée"), ("sudoc", "electronic", "électronique seule"),
    ("sudoc", "ambiguous_print_candidate", "ambigu"), ("sudoc", "etat_fantome", "indéterminé"),
    ("sudoc", None, "rien"), ("type", "these", "thèse"), ("provenance", "mémoire", "sans objet"),
    ("sudoc", "rien", "rien"),  # un libellé est accepté tel quel
])
def test_dictionnaire_des_faits(regles, fait, code, valeur):
    assert regles.faits[fait].traduire(code) == valeur


def test_quand_et_codes_bruts(regles):
    """Les règles s'écrivent `quand: {fait: valeur}` ; le banc d'essai accepte les codes bruts."""
    r = next(r for r in regles.decisions if r.nom == "Thèse EPHE, reproduction au Sudoc")
    assert r.admises("sudoc") == ["reproduction imprimée", "reproduction microfiche"] and r.si is None
    assert r.texte_condition == "type : thèse ; provenance : EPHE ; sudoc : reproduction imprimée ou reproduction microfiche"
    c = choisir(regles, {}, codes={"type": "these", "provenance": "EPHE", "alma": "no_academic_record_found",
                                   "sudoc": "microfiche_reproduction"})
    assert c.regle["nom"] == "Thèse EPHE, reproduction au Sudoc"
    assert [(t.fait, t.code, t.valeur) for t in c.traductions][-1] == ("sudoc", "microfiche_reproduction",
                                                                      "reproduction microfiche")


# ---------------------------------------------------------------- réponses des services
def test_alma_et_sudoc_juges_par_les_faits(regles):
    r = routage_service(regles, "sudoc", "printed_original")
    assert r.routage is None and not r.bloquant and r.libelle == "originale imprimée"


@pytest.mark.parametrize("service", ["idref_personne", "idref_organisations"])
@pytest.mark.parametrize("statut, libelle, issue, exception", [
    ("accepted", "alignée", FIN, False), ("low_confidence", "confiance faible", REP, True),
    ("ambiguous", "ambiguë", REP, True), ("not_found", "introuvable", REP, True),
])
def test_sorties_idref(regles, service, statut, libelle, issue, exception):
    """Les 4 statuts d'idref-linker, personnes et structures : jamais bloquants."""
    r = routage_service(regles, service, statut)
    assert (r.libelle, r.routage, r.bloquant, r.exception) == (libelle, issue, False, exception)
    assert r.alertes == (["proto-notice d'autorité à créer"] if statut == "not_found" else [])


@pytest.mark.parametrize("service, statut, issue, bloquant, exception", [
    ("dewey", "au_dessus_seuil", FIN, False, False),
    ("dewey", "sous_seuil", REP, False, True),
    ("dewey", "aucune_classe", REP, False, True),
    ("extraction", "json_valide", FIN, False, False),
    ("extraction", "json_invalide", "incident", True, False),
])
def test_routage_service(regles, service, statut, issue, bloquant, exception):
    r = routage_service(regles, service, statut)
    assert (r.routage, r.bloquant, r.exception) == (issue, bloquant, exception)


@pytest.mark.parametrize("service, statut", [("dewey", "statut_fantome"), ("service_fantome", "accepted")])
def test_statut_inconnu_incident(regles, service, statut):
    r = routage_service(regles, service, statut)
    assert (r.routage, r.bloquant) == ("incident", True)


# ---------------------------------------------------------------- issue d'un dossier
@pytest.mark.parametrize("routages, final", [
    ([FIN, REP, FIN], REP),
    ([REP, "incident"], "incident"),
    ([FIN, None], FIN),
    ([], FIN),
    (["auto", "décision", "sortie", "conversion"], REP),  # ancien vocabulaire traduit
    (["sortie"], FIN),
])
def test_routage_final(routages, final):
    assert routage_final(routages) == final


def test_routage_final_inconnu():
    with pytest.raises(ValueError):
        routage_final([FIN, "peut-être"])


def test_dossier_enregistre_avant_le_06_11_traduit():
    """Un dossier enregistré avec l'ancien routage est relu avec la nouvelle issue."""
    from humatheque.workflow.modeles import Dossier

    brut = {"id": "D", "document": {"id": "x", "images": [], "type_document": "these"}, "routage": "décision",
            "etapes": {"alma": {"nom": "alma", "statut": "arret", "routage": "sortie"}}}
    d = Dossier.model_validate(brut)
    assert d.routage == REP and d.etapes["alma"].routage == FIN


def test_ancien_identifiant_de_regle_traduit():
    from humatheque.decision.moteur import Decision

    d = Decision.model_validate({"situation": "S12", "suite": "creer", "routage": "auto", "motif": "S12 : Créer"})
    assert d.situation == "Mémoire absent du Sudoc"
