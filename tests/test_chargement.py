"""Chargement de la spec depuis les CSV (SPEC §8)."""

import pytest

from humatheque.spec import SpecIntrouvable, charger_spec


def test_charge_les_cinq_tables_de_spec():
    spec = charger_spec(source="csv")
    assert spec.source == "csv"
    assert {r.id for r in spec.regles} >= {"R001", "R051"}
    assert {p.code for p in spec.profils} == {
        "these_originale_imprimee", "these_reproduction_imprimee", "memoire_original_imprime"}
    assert spec.situations and spec.statuts_services and spec.sources
    assert len(spec.empreinte) == 12 and spec.empreinte in spec.version


def test_cellules_decoupees():
    spec = charger_spec(source="csv")
    r044 = next(r for r in spec.regles if r.id == "R044")
    assert r044.liste_profils() == [
        "these_originale_imprimee", "these_reproduction_imprimee", "memoire_original_imprime"]
    assert r044.liste_sources() == ["author", "ppn_personne", "nom_prenom"]


def test_entete_cardinalite_avec_ou_sans_accent(spec_fabriquee):
    avec = charger_spec(source="csv", dossier=spec_fabriquee.dossier)
    spec_fabriquee.renommer_colonne("Sources", "cardinalité", "cardinalite")
    sans = charger_spec(source="csv", dossier=spec_fabriquee.dossier)
    assert [s.cardinalite for s in avec.sources] == [s.cardinalite for s in sans.sources]
    assert next(s for s in sans.sources if s.id == "subtitle").cardinalite == "n"


def test_empreinte_stable_et_sensible_au_contenu(spec_fabriquee):
    a = charger_spec(source="csv", dossier=spec_fabriquee.dossier)
    b = charger_spec(source="csv", dossier=spec_fabriquee.dossier)
    assert a.empreinte == b.empreinte
    spec_fabriquee.modifier("Regles", "id", "R001", statut="validé")
    assert charger_spec(source="csv", dossier=spec_fabriquee.dossier).empreinte != a.empreinte


@pytest.mark.parametrize("table", ["Profils", "Statuts_services", "Regles"])
def test_table_manquante_message_explicite(spec_fabriquee, table):
    (spec_fabriquee.dossier / f"{table}.csv").unlink()
    with pytest.raises(SpecIntrouvable, match=f"{table}.csv"):
        charger_spec(source="csv", dossier=spec_fabriquee.dossier)
