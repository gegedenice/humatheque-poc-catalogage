"""Conversion v1 = copie de la notice source, conservée en ISO 2709 (SPEC §7)."""

import pymarc
import pytest

import services_simules as sim
from humatheque.conversion import (
    ConversionEchec, convertir, depuis_iso, lire_unimarc_xml, unimarc_xml_vers_iso, vers_winibw,
)


def test_xml_vers_iso_aller_retour():
    iso = unimarc_xml_vers_iso(sim.notice_xml())
    assert iso[:5].isdigit() and iso.endswith("\x1d")
    notice = depuis_iso(iso)
    source = lire_unimarc_xml(sim.notice_xml())
    assert [f.tag for f in notice.fields] == [f.tag for f in source.fields]
    assert notice["001"].data == "229969437"
    # Les caractères de non-classement du Sudoc (\x98 … \x9c) sont conservés tels quels.
    assert notice["200"]["a"] == source["200"]["a"] == "\x98La \x9cquestion de l'hygiène aux Indes-Néerlandaises"


def test_iso_lisible_par_pymarc():
    iso = unimarc_xml_vers_iso(sim.notice_xml())
    assert len(list(pymarc.MARCReader(iso.encode("utf-8"), to_unicode=True, force_utf8=True))) == 1


def test_rendu_winibw():
    lignes = vers_winibw(lire_unimarc_xml(sim.notice_xml())).splitlines()
    assert "001 229969437" in lignes
    assert "029 ##$aFR$b2017EHES0033" in lignes
    assert lignes == sorted(lignes, key=lambda l: l[:3])


def test_convertir_copie_avec_alerte():
    iso = unimarc_xml_vers_iso(sim.notice_xml())
    n = convertir(iso, "these_originale_imprimee")
    assert n.iso == iso
    assert n.texte == vers_winibw(depuis_iso(iso))
    assert n.alertes == ["conversion_non_implementee"]
    assert n.profil_cible == "these_originale_imprimee"


@pytest.mark.parametrize("xml", ["pas du xml", "<collection/>", "<record></record>"])
def test_notice_illisible(xml):
    with pytest.raises(ConversionEchec):
        unimarc_xml_vers_iso(xml)


def test_iso_illisible():
    with pytest.raises(ConversionEchec):
        depuis_iso("n'importe quoi")
