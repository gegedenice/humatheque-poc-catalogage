"""Vérificateur de spec (SPEC §8) : spec/ actuelle, puis un CSV fabriqué par défaut."""

import pytest

from humatheque.spec import charger_spec, verifier
from humatheque.spec.verificateur import etiquettes_zone


def _rapport(spec_fabriquee):
    return verifier(charger_spec(source="csv", dossier=spec_fabriquee.dossier))


def _codes(rapport, gravite="erreur"):
    return {(c.code, c.ligne) for c in rapport.constats if c.gravite == gravite}


def test_spec_actuelle_sans_erreur_bloquante():
    rapport = verifier(charger_spec(source="csv"))
    assert not rapport.bloquant, [c.message for c in rapport.erreurs]


def test_spec_actuelle_avertit_des_statuts_alma_sudoc_ignores():
    rapport = verifier(charger_spec(source="csv"))
    assert {c.ligne for c in rapport.avertissements if c.code == "statut_ignore"} == {
        "alma-checker", "sudoc-checker"}


@pytest.mark.parametrize("regle, cellules, code", [
    # Défauts relevés dans les premiers exports (corrigés dans Grist depuis)
    ("R009", {"gabarit": "105 ##$a"}, "gabarit_invalide"),
    ("R032", {"gabarit": "328 #0$zReproduction de…"}, "gabarit_invalide"),
    # Gabarits non analysables
    ("R022", {"gabarit": "200 1#$a{titre_classement} | {title}"}, "gabarit_invalide"),
    ("R022", {"gabarit": "200 1#$a{?}"}, "gabarit_invalide"),
    ("R027", {"repli": "Voir 100$a"}, "gabarit_invalide"),
    ("R022", {"gabarit": "210 1#$a{titre_classement}"}, "gabarit_invalide"),  # zone incohérente
    ("R033", {"gabarit": "", "mode_production": "Extraction"}, "gabarit_invalide"),
    # Variables absentes de Sources
    ("R051", {"repli": "711 02$a{co_tutelle_institution}$4995"}, "variable_inconnue"),
    ("R022", {"gabarit": "200 1#$a{titre_inexistant}"}, "variable_inconnue"),
    ("R022", {"sources": "title, source_inexistante"}, "variable_inconnue"),
    ("R019", {"gabarit": "181 ##$P01$c{ppn}"}, "variable_inconnue"),  # {ppn} sans source personne
    # Profils inconnus
    ("R005", {"profils": "these_originale_imprimee, profil_fantome"}, "profil_inconnu"),
    ("R005", {"profils": ""}, "profil_inconnu"),
    # Zone
    ("R040", {"zone": "600-607"}, "zone_invalide"),
    ("R040", {"zone": "607->600"}, "zone_invalide"),
])
def test_defaut_de_regle_detecte(spec_fabriquee, regle, cellules, code):
    spec_fabriquee.modifier("Regles", "id", regle, **cellules)
    assert (code, regle) in _codes(_rapport(spec_fabriquee))


def test_variables_d_element_admises_pour_personnes_et_organismes(spec_fabriquee):
    rapport = _rapport(spec_fabriquee)
    for r in ("R044", "R048", "R050", "R051"):  # {ppn}, {nom}, {prenom}
        assert not [c for c in rapport.erreurs if c.ligne == r]


def test_regle_rejetee_non_verifiee(spec_fabriquee):
    spec_fabriquee.modifier("Regles", "id", "R009", gabarit="105 ##$a", statut="rejeté")
    assert not _rapport(spec_fabriquee).bloquant


def test_cardinalite_inconnue(spec_fabriquee):
    spec_fabriquee.modifier("Sources", "id", "reviewers", **{"cardinalité": "plusieurs"})
    assert ("cardinalite_inconnue", "reviewers") in _codes(_rapport(spec_fabriquee))


def test_profil_cible_inconnu_dans_situations(spec_fabriquee):
    spec_fabriquee.modifier("Situations", "id", "S08", profil_cible="these_inconnue")
    assert ("profil_inconnu", "S08") in _codes(_rapport(spec_fabriquee))


@pytest.mark.parametrize("valeur", [
    "reproduction imprimée ou reproduction microfiche",  # S07 avant correction
    "reproduction",
    "originale imprimée | électronique",
])
def test_etat_sudoc_non_admis(spec_fabriquee, valeur):
    spec_fabriquee.modifier("Situations", "id", "S07", etat_sudoc=valeur)
    assert ("etat_sudoc_non_admis", "S07") in _codes(_rapport(spec_fabriquee))


def test_etat_sudoc_alias_et_alternatives_admis(spec_fabriquee):
    spec_fabriquee.modifier("Situations", "id", "S02", etat_sudoc="originale imprimée seule")
    spec_fabriquee.modifier("Situations", "id", "S07",
                            etat_sudoc="reproduction imprimée | reproduction microfiche | indéterminé")
    assert not _rapport(spec_fabriquee).bloquant


def test_etat_alma_non_admis(spec_fabriquee):
    spec_fabriquee.modifier("Situations", "id", "S01", etat_alma="présent (avec ou sans PPN)")
    assert ("etat_alma_non_admis", "S01") in _codes(_rapport(spec_fabriquee))


def test_provenance_et_routage_non_admis(spec_fabriquee):
    spec_fabriquee.modifier("Situations", "id", "S08", provenance="EPHE Ve section", routage="automatique")
    codes = _codes(_rapport(spec_fabriquee))
    assert {("provenance_non_admis", "S08"), ("routage_inconnu", "S08")} <= codes


def test_statut_alma_sudoc_autre_que_error_avertissement(spec_fabriquee):
    spec_fabriquee.ajouter("Statuts_services", service="sudoc-checker", statut="duplicate_found",
                           routage="selon Situations")
    rapport = _rapport(spec_fabriquee)
    assert not rapport.bloquant  # ligne ignorée : son routage n'est pas contrôlé
    sudoc = next(c for c in rapport.avertissements if c.ligne == "sudoc-checker")
    assert "duplicate_found" in sudoc.message


def test_routage_inconnu_dans_statuts_services_lus(spec_fabriquee):
    spec_fabriquee.ajouter("Statuts_services", service="dewey-classifier", statut="x", routage="peut-être")
    assert ("routage_inconnu", "dewey-classifier / x") in _codes(_rapport(spec_fabriquee))


def test_source_calculee_inutilisee(spec_fabriquee):
    # R028 avant correction : `volume` au lieu de la source calculée `volumaison`
    spec_fabriquee.modifier("Regles", "id", "R028", sources="volume", gabarit="215 ##$a{volume} volume")
    rapport = _rapport(spec_fabriquee)
    assert ("source_calculee_inutilisee", "volumaison") in _codes(rapport, "avertissement")


@pytest.mark.parametrize("zone, attendu", [
    ("200$a", ["200"]), ("104", ["104"]), ("600->602", ["600", "601", "602"]),
    ("600-607", None), ("20", None), ("200$", None),
])
def test_etiquettes_zone(zone, attendu):
    assert etiquettes_zone(zone, "->") == attendu
