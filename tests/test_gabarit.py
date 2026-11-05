"""Syntaxe stricte de `gabarit` et `repli` (SPEC §8)."""

import pytest

from humatheque.spec.gabarit import GabaritInvalide, analyser


@pytest.mark.parametrize("texte, etiquette, indicateurs, sous_zones, variables", [
    ("008 $aAax3", "008", None, [("a", "Aax3")], []),
    ("200 1#$a{titre_classement}", "200", "1#", [("a", "{titre_classement}")], ["titre_classement"]),
    ("$e{subtitle}", None, None, [("e", "{subtitle}")], ["subtitle"]),
    ("$bm$ba", None, None, [("b", "m"), ("b", "a")], []),
    ("181 ##$P01$ctxt", "181", "##", [("P", "01"), ("c", "txt")], []),
    ("701 #1$a{nom}$b{prenom}$4958$4555", "701", "#1",
     [("a", "{nom}"), ("b", "{prenom}"), ("4", "958"), ("4", "555")], ["nom", "prenom"]),
    ("$b{defense_year}{code_etab}{numero_nnt}", None, None,
     [("b", "{defense_year}{code_etab}{numero_nnt}")], ["defense_year", "code_etab", "numero_nnt"]),
    ("$d= {parallel_title}$z{parallel_title_lang}", None, None,
     [("d", "= {parallel_title}"), ("z", "{parallel_title_lang}")], ["parallel_title", "parallel_title_lang"]),
    ("214 #1$d19..", "214", "#1", [("d", "19..")], []),
])
def test_gabarits_valides(texte, etiquette, indicateurs, sous_zones, variables):
    g = analyser(texte)
    assert (g.etiquette, g.indicateurs, g.sous_zones, g.variables) == (etiquette, indicateurs, sous_zones, variables)


@pytest.mark.parametrize("texte, motif", [
    ("105 ##$a", "sans valeur"),                       # R009 avant correction
    ("328 #0$zReproduction de…", "interdit"),          # R032 avant correction
    ("$a{titre} | {autre}", "interdit"),
    ("$a{?}", "interdit"),
    ("$aEst-ce ?", "interdit"),
    ("$a$b{x}", "sans valeur"),
    ("Voir 100$a", "début attendu"),
    ("Si date absente : 100 0#$a19XX", "début attendu"),
    ("200 1#", "début attendu"),
    ("20 1#$ax", "début attendu"),
    ("$a{titre", "variable mal formée"),
    ("$a{nom prenom}", "variable mal formée"),
    ("", "vide"),
])
def test_gabarits_invalides(texte, motif):
    with pytest.raises(GabaritInvalide, match=motif):
        analyser(texte)
