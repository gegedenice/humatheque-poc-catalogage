"""Générateur (SPEC §8, §11 étape 3) sur deux contextes écrits à la main."""

import copy
import xml.etree.ElementTree as ET

import pytest

from contextes import REPRODUCTION_HORS_EPHE, THESE_ORIGINALE_EPHE
from humatheque.generateur import GenerationEchec, Generateur, generer, vers_marcxml, vers_winibw
from humatheque.spec import charger_spec

ORIG, REPRO = "these_originale_imprimee", "these_reproduction_imprimee"


@pytest.fixture(scope="module")
def spec():
    return charger_spec(source="csv")


@pytest.fixture(scope="module")
def originale(spec):
    return generer(spec, THESE_ORIGINALE_EPHE, ORIG)


@pytest.fixture(scope="module")
def reproduction(spec):
    return generer(spec, REPRODUCTION_HORS_EPHE, REPRO)


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
    rapporteurs = [z for z in originale.zones_de("701") if z.libelle == "Rapporteurs"]
    assert [z.texte() for z in rapporteurs] == [
        "701 #1$3TEST00004$4958$4555",
        "701 #1$aDurand$bClaire$4958$4555",
    ]
    assert "R048" in originale.replis


# --- Rendu détaillé
def test_notice_originale_complete(originale):
    assert vers_winibw(originale).splitlines() == [
        "008 $aAax3",
        "029 ##$aFR$b1998EPHEXXXX",
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
        "$fJean-Daniel DUBOIS$gsous la direction de Marie-France de LA ROCHE, Paul MARTIN",
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
    assert originale.alertes == []


def test_profil_reproduction_sans_029_et_avec_cotutelles(reproduction):
    assert not reproduction.zones_de("029")
    assert _lignes(reproduction, "711") == [
        "711 02$aUniversité de Montpellier$4295",
        "711 02$3TEST00200$4995",
        "711 02$aUniversité de Liège$4995",
    ]


def test_directeurs_multiples_un_seul_g(originale):
    (z200,) = originale.zones_de("200")
    assert [s.valeur for s in z200.sous_zones if s.code == "g"] == [
        "sous la direction de Marie-France de LA ROCHE, Paul MARTIN"]


def test_sous_titre_repete_dans_la_meme_200(originale):
    (z200,) = originale.zones_de("200")
    assert [s.valeur for s in z200.sous_zones if s.code == "e"] == ["le cas du Sénégal", "1960-1990"]


def test_zones_annotees_par_regle_et_statut(originale):
    (z105,) = originale.zones_de("105")
    assert [r.id for r in z105.regles] == ["R009", "R010", "R013", "R014", "R015", "R016", "R017"]
    assert {r.statut for r in z105.regles} == {"seed", "à discuter"}
    assert "R010 [à discuter]" in vers_winibw(originale, annoter=True)


def test_non_genere_liste_comme_non_couverte(originale):
    non = {r.regle for r in originale.non_couvertes}
    assert {"R006", "R029", "R037", "R040"} <= non
    assert not originale.zones_de("330")


def test_regle_prioritaire_omise_alerte(spec):
    ctx = copy.deepcopy(THESE_ORIGINALE_EPHE) | {"titre_classement": None, "dewey_code": None}
    n = generer(spec, ctx, ORIG)
    assert {"regle_prioritaire_omise:R022", "regle_prioritaire_omise:R043"} <= set(n.alertes)
    assert not n.zones_de("686")
    # 200 ouverte par le premier fragment, avec les indicateurs déclarés pour la zone
    assert n.zones_de("200")[0].texte().startswith("200 1#$ele cas du Sénégal")


def test_repli_de_zone(spec):
    ctx = copy.deepcopy(THESE_ORIGINALE_EPHE) | {"defense_year": None}
    n = generer(spec, ctx, ORIG)
    assert _lignes(n, "100") == ["100 0#$a19XX"]
    assert _lignes(n, "214") == ["214 #1$d19.."]
    assert _lignes(n, "328")[0].endswith("$d19..")
    assert not n.zones_de("029")[0].texte().count("$b")  # 029$b sans année : omise


def test_personne_sans_ppn_ni_nom_omise(spec):
    ctx = copy.deepcopy(THESE_ORIGINALE_EPHE)
    ctx["reviewers"] = [{"forme": "X", "nom": None, "prenom": None, "ppn": None}]
    n = generer(spec, ctx, ORIG)
    assert not [z for z in n.zones_de("701") if z.libelle == "Rapporteurs"]


def test_statut_rejete_exclu(spec):
    gen = Generateur(spec, statuts_exclus=["rejeté", "à discuter"])
    n = gen.generer(THESE_ORIGINALE_EPHE, ORIG)
    assert _lignes(n, "105") == ["105 ##$c0$d0$fy"]
    assert not n.zones_de("686")


def test_profil_inconnu(spec):
    with pytest.raises(GenerationEchec, match="profil"):
        generer(spec, THESE_ORIGINALE_EPHE, "profil_fantome")


def test_gabarit_invalide_echec(spec_fabriquee):
    spec_fabriquee.modifier("Regles", "id", "R005", gabarit="101 0#$a")
    spec = charger_spec(source="csv", dossier=spec_fabriquee.dossier)
    with pytest.raises(GenerationEchec, match="R005"):
        generer(spec, THESE_ORIGINALE_EPHE, ORIG)


def test_memoire_directeur_003(spec):
    n = generer(spec, THESE_ORIGINALE_EPHE, "memoire_original_imprime")
    assert _lignes(n, "701")[:2] == ["701 #1$3TEST00002$4003", "701 #1$aMartin$bPaul$4003"]
    assert not n.zones_de("686")


def test_marcxml(originale):
    xml = vers_marcxml(originale)
    racine = ET.fromstring(xml)
    ns = {"m": "http://www.loc.gov/MARC21/slim"}
    assert racine.find("m:controlfield[@tag='008']", ns).text == "Aax3"
    f700 = racine.find("m:datafield[@tag='700']", ns)
    assert (f700.get("ind1"), f700.get("ind2")) == (" ", "1")
    assert [sf.get("code") for sf in f700.findall("m:subfield", ns)] == ["3", "4"]
    assert len(racine.findall("m:datafield[@tag='701']", ns)) == 5
