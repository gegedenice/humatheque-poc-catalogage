"""Chargement et vérificateur des règles (SPEC §8.1, §8.4, §11 étape 1).

Règles réelles : aucune erreur et exactement les 4 cas non couverts ; puis un
fichier fabriqué par défaut du tableau du §8.4.
"""

import pytest

from humatheque.regles import ReglesInvalides, charger_regles, verifier
from humatheque.regles.verificateur import verifier_textes


def _codes(rapport, gravite=None):
    return [c.code for c in rapport.constats if gravite is None or c.gravite == gravite]


# ---------------------------------------------------------------- règles réelles
def test_chargement_regles_reelles(regles):
    assert set(regles.profils) == {"these-originale-imprimee", "these-reproduction-imprimee",
                                   "memoire-original-imprime"}
    assert len(regles.socle.zones) == 34
    assert list(regles.etapes) == ["provenance", "alma", "sudoc", "idref", "dewey", "creation"]
    assert regles.etapes["alma"].variables["status"][0] == "academic_record_found"
    assert regles.etapes["sudoc"].sinon.resultat == "stop à reprendre"
    assert regles.variables["auteur"].type == "personne" and regles.variables["directeur"].liste
    assert not regles.variables["numero_nnt"].disponible
    assert len(regles.empreinte) == 12 and regles.version.endswith(regles.empreinte)
    z = regles.profils["these-originale-imprimee"].zones[0]
    assert (z.id, z.zone, z.trace, z.statut, z.indicateurs) == ("008", "008", "008 (socle)", "validée", None)
    z328 = next(z for z in regles.socle.zones if z.id == "328")
    assert [(sz.code, sz.genre, sz.presence) for sz in z328.sous_zones][-1] == ("d", "variable", "repli « 19.. » si absente")


def test_aucune_erreur_et_cas_tombant_dans_sinon(regles):
    """Cas Sudoc non prévus (stop à reprendre) : signalés, rien d'autre."""
    r = verifier(regles)
    assert not r.erreurs and not r.avertissements
    cas = sorted(c.message for c in r.constats if c.code == "cas_sinon")
    assert cas == [
        "tombe dans « sinon » : type = these, ephe = non, status = duplicate_found, thesis_state = printed_reproduction",
        "tombe dans « sinon » : type = these, ephe = oui, status = duplicate_found, thesis_state = printed_original",
        "tombe dans « sinon » : type = these, status = duplicate_found, thesis_state = electronic",
    ]
    assert {c.code for c in r.constats if c.gravite == "info"} == {"variable_inutilisee", "cas_sinon"}


def test_empreinte_stable_et_sensible(regles_fabriquees):
    a, b = regles_fabriquees.charger(), regles_fabriquees.charger()
    assert a.empreinte == b.empreinte
    regles_fabriquees.modifier_etape("dewey", libelle="Classification Dewey")
    assert regles_fabriquees.charger().empreinte != a.empreinte


# ---------------------------------------------------------------- défauts fabriqués (§8.4)
def test_yaml_invalide_avec_ligne(regles_fabriquees):
    regles_fabriquees.ecrire("pipeline.yaml", regles_fabriquees.texte("pipeline.yaml") + "\n  x: {si: [\n")
    with pytest.raises(ReglesInvalides) as e:
        regles_fabriquees.charger()
    (fichier, ligne, message), = e.value.problemes
    assert fichier == "pipeline.yaml" and ligne.startswith("ligne ") and "YAML invalide" in message


def test_structure_absente(regles_fabriquees):
    regles_fabriquees.ecrire("notice/socle.yaml", "zone: x\n")
    with pytest.raises(ReglesInvalides, match="clés rendus et zones attendues"):
        regles_fabriquees.charger()
    regles_fabriquees.ecrire("notice/socle.yaml", "zones: [{id: 1, zone: '100', inconnue: 2}]\n")
    with pytest.raises(ReglesInvalides, match="inconnue"):
        regles_fabriquees.charger()


def test_pipeline_structure(regles_fabriquees):
    regles_fabriquees.ecrire("pipeline.yaml", "etapes: [1]\n")
    with pytest.raises(ReglesInvalides, match="clés communes et etapes attendues"):
        regles_fabriquees.charger()


@pytest.mark.parametrize("zone, code", [
    ({"id": "x", "zone": "10", "sous_zones": [{"code": "a", "constante": "X"}]}, "zone_illisible"),
    ({"id": "x", "zone": "330", "indicateurs": "###", "sous_zones": [{"code": "a", "constante": "X"}]},
     "indicateurs_invalides"),
    ({"id": "x", "zone": "330", "indicateurs": "##", "sous_zones": [{"code": "a", "variable": "inexistante"}]},
     "variable_inconnue"),
    ({"id": "x", "zone": "330", "indicateurs": "##", "sous_zones": [{"code": "a", "variable": "titre", "attribut": "ppn"}]},
     "attribut_impossible"),
    ({"id": "x", "zone": "701", "indicateurs": "#1", "sous_zones": [{"code": "3", "autorite": "directeur"},
                                                                     {"code": "3", "autorite": "rapporteur"}]},
     "repetition_multiple"),
    ({"id": "x", "zone": "330", "indicateurs": "##", "sous_zones": [{"code": "a", "autorite": "titre"}]},
     "autorite_invalide"),
    ({"id": "x", "zone": "330", "indicateurs": "##", "sous_zones": [{"code": "a", "variable": "titre",
                                                                     "constante": "X"}]}, "valeur_ambigue"),
    ({"id": "x", "zone": "330", "indicateurs": "##", "sous_zones": [{"code": "a", "variable": "titre",
                                                                     "si_absente": "repli"}]}, "repli_absent"),
    ({"id": "x", "zone": "330", "indicateurs": "##", "sous_zones": [{"code": "a"}]}, "valeur_absente"),
    ({"id": "x", "zone": "700", "indicateurs": "#1", "sous_zones": [{"code": "3", "autorite": "auteur",
                                                                     "rendu": "fantaisie"}]}, "rendu_inconnu"),
    ({"id": "328", "zone": "328", "sous_zones": [{"code": "a", "constante": "X"}]}, "zone_doublon"),
])
def test_defauts_de_zone(regles_fabriquees, zone, code):
    regles_fabriquees.ajouter_zone("socle", zone)
    assert code in _codes(verifier(regles_fabriquees.charger()), "erreur")


def test_defauts_de_variante(regles_fabriquees):
    v = regles_fabriquees.lire("notice/variantes/memoire-original-imprime.yaml")
    v["retirer"].append("999")
    v["remplacer"].append({"id": "998", "zone": "998", "sous_zones": [{"code": "a", "constante": "X"}]})
    v.setdefault("ajouter", []).append({"id": "328", "zone": "328", "sous_zones": [{"code": "a", "constante": "X"}]})
    regles_fabriquees.ecrire("notice/variantes/memoire-original-imprime.yaml", v)
    codes = _codes(verifier(regles_fabriquees.charger()), "erreur")
    assert codes.count("zone_inconnue") == 2 and "zone_doublon" in codes


@pytest.mark.parametrize("noeud, code", [
    ({"si": {"status": "statut_fantome"}, "resultat": "stop"}, "valeur_inconnue"),
    ({"si": {"variable_fantome": "x"}, "resultat": "stop"}, "variable_inconnue"),
    ({"si": {"status": "academic_record_found"}, "resultat": "création", "notice": "fantome"}, "notice_inconnue"),
    ({"si": {"status": "academic_record_found"}}, "noeud_vide"),
    ({"si": {"status": "academic_record_found"}, "resultat": "stop",
      "branches": [{"resultat": "stop"}]}, "noeud_ambigu"),
    ({"sauf": {"type": "roman"}, "resultat": "stop"}, "valeur_inconnue"),
])
def test_defauts_de_pipeline(regles_fabriquees, noeud, code):
    regles_fabriquees.ajouter_branche("alma", noeud, 0)
    assert code in _codes(verifier(regles_fabriquees.charger()), "erreur")


def test_resultat_inconnu_refuse_au_chargement(regles_fabriquees):
    regles_fabriquees.ajouter_branche("alma", {"si": {"status": "academic_record_found"}, "resultat": "arrêt"})
    with pytest.raises(ReglesInvalides, match="resultat"):
        regles_fabriquees.charger()


def test_notice_non_atteinte(regles_fabriquees):
    regles_fabriquees.modifier_etape("creation", branches=[{"si": {"type": "these"}, "resultat": "création",
                                                            "notice": "these-originale-imprimee"}])
    regles_fabriquees.modifier_etape("sudoc", branches=[])
    r = verifier(regles_fabriquees.charger())
    assert "notice_non_atteinte" in _codes(r, "avertissement") and not r.bloquant


def test_cas_sinon_comble(regles_fabriquees):
    regles_fabriquees.ajouter_branche("sudoc", {"si": {"type": "these", "ephe": "non",
                                                       "thesis_state": "printed_reproduction"}, "resultat": "stop"}, 0)
    r = verifier(regles_fabriquees.charger())
    assert sum(c.code == "cas_sinon" for c in r.constats) == 2


def test_verifier_un_brouillon_sans_ecrire(regles_fabriquees):
    texte = regles_fabriquees.texte("pipeline.yaml").replace("status: academic_record_found",
                                                             "status: academic_record_foundd")
    r = verifier_textes({"pipeline.yaml": texte}, regles_fabriquees.reglages)
    assert r.bloquant and "valeur_inconnue" in _codes(r)
    assert regles_fabriquees.texte("pipeline.yaml") != texte  # rien n'est écrit
    r = verifier_textes({"pipeline.yaml": "etapes: [\n"}, regles_fabriquees.reglages)
    assert _codes(r) == ["yaml_invalide"]


def test_regles_dir(regles_fabriquees):
    (regles_fabriquees.racine / "autres").mkdir()
    import shutil
    shutil.copytree(regles_fabriquees.dossier, regles_fabriquees.racine / "autres" / "regles")
    from humatheque.config import Reglages
    r = charger_regles(Reglages(_env_file=None, racine=regles_fabriquees.racine, regles_dir="autres/regles"))
    assert r.emplacement.endswith("autres/regles")
