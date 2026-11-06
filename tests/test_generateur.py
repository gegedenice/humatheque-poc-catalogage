"""Générateur (SPEC §8.3, §11 étape 3) : profils de regles/ sur des contextes
écrits à la main, et tests de reference/moteur-regles/tests/."""

import copy
import json
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from contextes import REPRODUCTION_HORS_EPHE, THESE_ORIGINALE_EPHE
from humatheque.generateur import GenerationEchec, Generateur, generer, vers_marcxml, vers_winibw
from humatheque.regles.moteur import generer as remplir

ORIG, REPRO, MEM = "these-originale-imprimee", "these-reproduction-imprimee", "memoire-original-imprime"
EXEMPLE = json.loads((Path(__file__).resolve().parents[1] / "reference" / "moteur-regles"
                      / "exemple_contexte.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def originale(regles):
    return generer(regles, THESE_ORIGINALE_EPHE, ORIG)


@pytest.fixture(scope="module")
def reproduction(regles):
    return generer(regles, REPRODUCTION_HORS_EPHE, REPRO)


def _lignes(notice, etiquette):
    return [z.texte() for z in notice.zones_de(etiquette)]


# --- Tests de référence du §11, étape 3
def test_105_une_seule_occurrence(originale, reproduction):
    assert _lignes(originale, "105") == ["105 ##$aa$bm$ba$c0$d0$e0$fy$gy"]
    assert _lignes(reproduction, "105") == ["105 ##$aa$bv$ba$c0$d0$e0$fy$gy"]


def test_328_reproduction_de_en_tete(originale, reproduction):
    assert _lignes(reproduction, "328") == [
        "328 #0$zReproduction de$bThèse de doctorat$cScience politique$eUniversité de Montpellier$d2005"]
    assert _lignes(originale, "328") == [
        "328 #0$bThèse de doctorat$cScience politique$eÉcole pratique des hautes études$d1998"]


def test_une_701_par_rapporteur_et_repli_sans_ppn(originale):
    rapporteurs = [z for z in originale.zones_de("701") if z.libelle == "rapporteur"]
    assert [z.texte() for z in rapporteurs] == ["701 #1$3TEST00004$4958$4555", "701 #1$aDurand$bClaire$4958$4555"]
    assert "R048" in originale.replis


def test_200g_unique_a_et_b(originale, regles):
    """Décision du 06/11 : « sous la direction de A et B », « A, B et C »."""
    (z200,) = originale.zones_de("200")
    assert [s.valeur for s in z200.sous_zones if s.code == "g"] == [
        "sous la direction de Marie-France de LA ROCHE et Paul MARTIN"]
    trois = copy.deepcopy(THESE_ORIGINALE_EPHE)
    trois["advisor"].append({"forme": "Jean Bon", "nom": "Bon", "prenom": "Jean", "ppn": None})
    (z200,) = generer(regles, trois, ORIG).zones_de("200")
    assert z200.texte().endswith("$gsous la direction de Marie-France de LA ROCHE, Paul MARTIN et Jean Bon")
    un = copy.deepcopy(THESE_ORIGINALE_EPHE) | {"advisor": [THESE_ORIGINALE_EPHE["advisor"][1]]}
    assert generer(regles, un, ORIG).zones_de("200")[0].texte().endswith("$gsous la direction de Paul MARTIN")


# --- Rendu détaillé
def test_notice_originale_complete(originale):
    assert vers_winibw(originale).splitlines() == [
        "008 $aAax3",
        "100 0#$a1998",
        "101 0#$afre",
        "102 ##$aFR",
        "104 ##$ak$by$cy$dba$e0$ffre",
        "105 ##$aa$bm$ba$c0$d0$e0$fy$gy",
        "106 ##$ar",
        "181 ##$P01$ctxt",
        "182 ##$P01$cn",
        "183 ##$P01$anga",
        "200 1#$aContribution à l'étude de la démocratie en Afrique$ele cas du Sénégal$e1960-1990"
        "$fJean-Daniel DUBOIS$gsous la direction de Marie-France de LA ROCHE et Paul MARTIN",
        "214 #1$d1998",
        "215 ##$a1 volume",
        "328 #0$bThèse de doctorat$cScience politique$eÉcole pratique des hautes études$d1998",
        "608 ##$3027253139$aThèses et écrits académiques$2rameau",
        "686 ##$a320$2TEF",
        "700 #1$3TEST00001$4070",
        "701 #1$3TEST00002$4727",
        "701 #1$aMartin$bPaul$4727",
        "701 #1$3TEST00003$4956$4555",
        "701 #1$3TEST00004$4958$4555",
        "701 #1$aDurand$bClaire$4958$4555",
        "711 02$3TEST00100$4295",
        "711 02$aÉcole doctorale de l'EPHE$4996",
    ]
    # numero_nnt indisponible : 029 omise (§8.3 règle 4), alerte de priorité 1 (pas de bouchon)
    assert originale.alertes == ["regle_prioritaire_omise:R002 R003"]


def test_profil_reproduction_sans_029_et_avec_cotutelles(reproduction):
    assert not reproduction.zones_de("029")
    assert _lignes(reproduction, "711") == [
        "711 02$aUniversité de Montpellier$4295",
        "711 02$3TEST00200$4995",
        "711 02$aUniversité de Liège$4995",
    ]
    assert reproduction.alertes == []


def test_sous_titre_repete_dans_la_meme_200(originale):
    (z200,) = originale.zones_de("200")
    assert [s.valeur for s in z200.sous_zones if s.code == "e"] == ["le cas du Sénégal", "1960-1990"]


def test_zones_annotees_par_ligne(originale):
    (z105,) = originale.zones_de("105")
    assert [(r.id, r.statut) for r in z105.regles] == [("R009-R017", "à discuter")]
    (z102,) = originale.zones_de("102")
    assert [(r.id, r.statut) for r in z102.regles] == [("R007", "seed")]
    assert "R009-R017 [à discuter]" in vers_winibw(originale, annoter=True)


def test_inactives_listees_comme_non_couvertes(originale):
    non = {r.regle for r in originale.non_couvertes}
    assert {"R023", "R029", "R037", "R040"} <= non
    assert not originale.zones_de("330") and not originale.zones_de("600")


def test_ligne_prioritaire_omise_alerte(regles):
    ctx = copy.deepcopy(THESE_ORIGINALE_EPHE) | {"titre_classement": None, "dewey": None, "author": []}
    n = generer(regles, ctx, ORIG)
    assert "regle_prioritaire_omise:R044" in n.alertes  # 700 sans auteur
    assert not n.zones_de("686") and "regle_prioritaire_omise:R043" in n.alertes
    assert n.zones_de("200")[0].texte().startswith("200 1#$ele cas du Sénégal")  # $a omis


def test_replis_annee(regles):
    """Replis d'année (ancienne spécification) : 100$a 19XX, 214$d et 328$d 19.. (seule $d a un repli en 328)."""
    n = generer(regles, copy.deepcopy(THESE_ORIGINALE_EPHE) | {"defense_year": None}, ORIG)
    assert _lignes(n, "100") == ["100 0#$a19XX"]
    assert _lignes(n, "214") == ["214 #1$d19.."]
    assert _lignes(n, "328") == ["328 #0$bThèse de doctorat$cScience politique$eÉcole pratique des hautes études$d19.."]
    n = generer(regles, copy.deepcopy(THESE_ORIGINALE_EPHE) | {"discipline": None}, ORIG)
    assert _lignes(n, "328") == ["328 #0$bThèse de doctorat$eÉcole pratique des hautes études$d1998"]


def test_personne_sans_ppn_ni_nom_omise(regles):
    ctx = copy.deepcopy(THESE_ORIGINALE_EPHE)
    ctx["reviewers"] = [{"forme": "X", "nom": None, "prenom": None, "ppn": None}]
    n = generer(regles, ctx, ORIG)
    assert not [z for z in n.zones_de("701") if z.libelle == "rapporteur"]


def test_statut_exclu(regles):
    n = Generateur(regles, statuts_exclus=["rejeté", "à discuter"]).generer(THESE_ORIGINALE_EPHE, ORIG)
    assert not n.zones_de("105") and not n.zones_de("686") and n.zones_de("102")


def test_profil_inconnu(regles):
    with pytest.raises(GenerationEchec, match="profil"):
        generer(regles, THESE_ORIGINALE_EPHE, "profil-fantome")


def test_condition_si_et_condition_invalide(regles_fabriquees):
    regles_fabriquees.ajouter_ligne(ORIG, {"ligne": "330 ##$a{resume}", "si": 'langue == "eng"', "ref": "T1"})
    regles_fabriquees.ajouter_ligne(ORIG, {"ligne": "541 ##$a{titre}", "si": 'langue == "fre"', "ref": "T2"})
    r = regles_fabriquees.charger()
    n = generer(r, THESE_ORIGINALE_EPHE | {"abstract": "Résumé"}, ORIG)
    assert not n.zones_de("330") and _lignes(n, "541") == ["541 ##$aContribution à l'étude de la démocratie en Afrique"]


def test_memoire_directeur_003(regles):
    n = generer(regles, THESE_ORIGINALE_EPHE, MEM)
    assert _lignes(n, "701")[:2] == ["701 #1$3TEST00002$4003", "701 #1$aMartin$bPaul$4003"]
    assert not n.zones_de("686")


def test_marcxml(originale):
    racine = ET.fromstring(vers_marcxml(originale))
    ns = {"m": "http://www.loc.gov/MARC21/slim"}
    assert racine.find("m:controlfield[@tag='008']", ns).text == "Aax3"
    f700 = racine.find("m:datafield[@tag='700']", ns)
    assert (f700.get("ind1"), f700.get("ind2")) == (" ", "1")
    assert [sf.get("code") for sf in f700.findall("m:subfield", ns)] == ["3", "4"]
    assert len(racine.findall("m:datafield[@tag='701']", ns)) == 5


# --- Tests de reference/moteur-regles/tests/ (contexte déjà au nom des variables)
def _zones(regles, profil):
    return [z.texte() for z in remplir(regles, profil, EXEMPLE).zones_triees()]


def test_reference_repli_et_repetition(regles):
    z = _zones(regles, ORIG)
    assert "700 #1$aDurand$bMarie$4070" in z
    assert "701 #1$3026594803$4727" in z
    assert sum(l.startswith("701 #1") and l.endswith("$4727") for l in z) == 2


def test_reference_zone_omise_si_donnee_absente(regles):
    z = _zones(regles, ORIG)
    assert not any(l.startswith("686") for l in z) and not any(l.startswith("029") for l in z)


def test_reference_profils_reproduction_et_memoire(regles):
    assert "105 ##$aa$bv$ba$c0$d0$e0$fy$gy" in _zones(regles, REPRO)
    assert any(l.startswith("328 #0$zReproduction de") for l in _zones(regles, REPRO))
    assert "701 #1$3026594803$4003" in _zones(regles, MEM)
