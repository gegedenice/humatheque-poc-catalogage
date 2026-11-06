"""Notice éditable (SPEC §9.2, §11 étape 6 ter) : analyse, validation, versions."""

import xml.etree.ElementTree as ET

import pytest

from contextes import THESE_ORIGINALE_EPHE
from humatheque.generateur import generer, vers_winibw
from humatheque.notice import analyser, valider, vers_marcxml
from humatheque.notice import versions as nv

ORIG = "these-originale-imprimee"


@pytest.fixture(scope="module")
def notice(regles):
    """Notice générée, PPN de test remplacés par des PPN bien formés."""
    texte = vers_winibw(generer(regles, THESE_ORIGINALE_EPHE, ORIG))
    for i in (1, 2, 3, 4, 100):
        texte = texte.replace(f"TEST{i:05d}", f"0{i:07d}X"[:9])
    return texte


# ---------------------------------------------------------------- syntaxe
@pytest.mark.parametrize("ligne", [
    "008 $aAax3",
    "001 229969437",                       # zone de contrôle sans sous-zone (notice convertie)
    "200 1#$aTitre$eSous-titre$fAuteur",
    "181 ##$P01$ctxt",
    "701 #1$aMartin$bPaul$4727",
    "035 ##$a(OCoLC)1582502304",
])
def test_lignes_valides(ligne):
    assert valider(ligne) == []


@pytest.mark.parametrize("ligne, colonne, motif", [
    ("20 1#$aTitre", 1, "3 chiffres"),
    ("2OO 1#$aTitre", 1, "3 chiffres"),
    ("200 $aTitre", 5, "indicateurs"),
    ("200 1#Titre", 5, "indicateurs"),
    ("200 1#$a", 7, "sans valeur"),
    ("200 1#$aTitre$e", 14, "sans valeur"),
    ("200 1#$aTitre$-x", 14, "invalide"),
    ("008 ", 1, "vide"),
    ("700 #1$3123$4070", 7, "PPN"),
    ("700 #1$302694034Z$4070", 7, "PPN"),
])
def test_lignes_invalides(ligne, colonne, motif):
    (m,) = valider(ligne)
    assert m.gravite == "erreur" and motif in m.message and m.colonne_debut == colonne and m.ligne == 1


def test_numeros_de_ligne_et_lignes_vides():
    m = valider("008 $aAax3\n\n200 1#$a")
    assert [x.ligne for x in m] == [3]


def test_notice_vide():
    assert valider("")[0].message == "notice vide"


# ---------------------------------------------------------------- règles du profil
def test_notice_generee_sans_marqueur(notice, regles):
    assert valider(notice, regles, ORIG) == []


def test_zone_prioritaire_absente(notice, regles):
    texte = "\n".join(l for l in notice.splitlines() if not l.startswith("101 "))
    (m,) = valider(texte, regles, ORIG)
    assert m.gravite == "erreur" and "zone 101 absente" in m.message and "ligne 4" in m.message


def test_zone_et_sous_zone_inconnues_avertissement(notice, regles):
    texte = notice + "\n999 ##$aquelque chose\n610 ##$aMot-clé"
    texte = texte.replace("101 0#$afre", "101 0#$afre$xbizarre")
    marqueurs = valider(texte, regles, ORIG)
    assert {m.gravite for m in marqueurs} == {"avertissement"}
    messages = " ".join(m.message for m in marqueurs)
    assert "zone 999 produite par aucune ligne" in messages and "101$x" in messages
    assert "610" not in messages  # ligne 610 (actif: false) documente la 610 pour ce profil


def test_vers_marcxml(notice):
    racine = ET.fromstring(vers_marcxml(notice + "\n001 229969437"))
    ns = {"m": "http://www.loc.gov/MARC21/slim"}
    assert racine.find("m:controlfield[@tag='001']", ns).text == "229969437"
    assert racine.find("m:controlfield[@tag='008']", ns).text == "Aax3"
    assert len(analyser(notice)) == len(notice.splitlines())


# ---------------------------------------------------------------- versions
def _v(versions, v):
    versions.append(v)
    return v


def test_cycle_generee_editee_validee():
    vs = []
    v1 = _v(vs, nv.enregistrer_production(vs, "D", "008 $aAax3", "générée"))
    assert (v1.numero, v1.statut, v1.auteur) == (1, "générée", "système")
    v2 = _v(vs, nv.editer(vs, "D", "008 $aAax3\n101 0#$afre", "cat", base=1))
    assert (v2.statut, v2.source) == ("éditée", "édition") and "+101 0#$afre" in v2.diff
    with pytest.raises(nv.ConflitVersion):
        nv.editer(vs, "D", "autre", "cat", base=1)
    with pytest.raises(nv.ValidationRefusee):
        nv.valider(vs, "D", "cat", erreurs=["une erreur"])
    v3 = _v(vs, nv.valider(vs, "D", "cat", erreurs=[]))
    assert v3.statut == "validée" and v3.texte == v2.texte
    assert nv.a_exporter(vs).numero == 3


def test_regeneration_remplace_une_version_seulement_generee():
    vs = []
    _v(vs, nv.enregistrer_production(vs, "D", "A", "générée"))
    assert nv.enregistrer_production(vs, "D", "A", "générée") is None
    v2 = _v(vs, nv.enregistrer_production(vs, "D", "B", "générée"))
    assert v2.statut == "générée" and nv.courante(vs).texte == "B"


def test_regeneration_n_ecrase_pas_une_edition():
    vs = []
    _v(vs, nv.enregistrer_production(vs, "D", "A", "générée"))
    _v(vs, nv.editer(vs, "D", "A édité", "cat", base=1))
    p = _v(vs, nv.enregistrer_production(vs, "D", "B", "générée"))
    assert p.statut == "proposée" and nv.courante(vs).texte == "A édité"
    assert nv.proposition(vs).numero == p.numero and "-A édité" in p.diff
    assert nv.enregistrer_production(vs, "D", "B", "générée") is None  # pas de doublon
    r = _v(vs, nv.reporter(vs, "D", p.numero, "cat"))
    vs[p.numero - 1] = vs[p.numero - 1].model_copy(update={"statut": "reportée"})
    assert (r.statut, r.texte, nv.proposition(vs)) == ("éditée", "B", None)


def test_a_exporter_sans_validation_version_generee():
    vs = []
    _v(vs, nv.enregistrer_production(vs, "D", "A", "convertie"))
    _v(vs, nv.editer(vs, "D", "A édité", "cat", base=1))
    assert nv.a_exporter(vs).texte == "A"
