"""Tests du moteur sur les règles réelles de regles/ : uv run --with pyyaml --with rule-engine --with pytest pytest reference/moteur-regles"""
import json
import sys
from pathlib import Path

ICI = Path(__file__).resolve().parent
sys.path.insert(0, str(ICI.parent))
import moteur  # noqa: E402

REGLES = moteur.charger(ICI.parents[2] / "regles")
CTX = json.loads((ICI.parent / "exemple_contexte.json").read_text(encoding="utf-8"))


def zones(profil, ctx=CTX):
    return moteur.generer(REGLES, profil, ctx)[0]


def test_aucune_erreur_dans_les_regles():
    assert not [m for g, m in moteur.verifier(REGLES) if g == "erreur"]


def test_cas_non_couverts_connus():
    trous = [m for g, m in moteur.verifier(REGLES) if m.startswith("cas non couvert")]
    assert len(trous) == 4


def test_decisions():
    assert moteur.decider(REGLES, {"type": "thèse", "provenance": "EPHE", "alma": "absent", "sudoc": "rien"})["id"] == "S08"
    assert moteur.decider(REGLES, {"type": "mémoire", "provenance": "sans objet", "alma": "présent", "sudoc": "rien"})["id"] == "S01"
    assert moteur.decider(REGLES, {"type": "thèse", "provenance": "EPHE", "alma": "absent", "sudoc": "originale imprimée"})["id"] == "sinon"


def test_repli_et_repetition():
    z = zones("these-originale-imprimee")
    assert "700 #1$aDurand$bMarie$4070" in z            # pas de PPN : repli
    assert "701 #1$3026594803$4727" in z                 # PPN : forme principale
    assert sum(l.startswith("701 #1") and l.endswith("$4727") for l in z) == 2   # une 701 par directeur


def test_zone_omise_si_donnee_absente():
    z = zones("these-originale-imprimee")
    assert not any(l.startswith("686") for l in z)       # pas de Dewey : pas de « 686 ##$2TEF »
    assert not any(l.startswith("029") for l in z)       # pas de NNT


def test_profils_reproduction_et_memoire():
    assert "105 ##$aa$bv$ba$c0$d0$e0$fy$gy" in zones("these-reproduction-imprimee")
    assert any(l.startswith("328 #0$zReproduction de") for l in zones("these-reproduction-imprimee"))
    assert "701 #1$3026594803$4003" in zones("memoire-original-imprime")
