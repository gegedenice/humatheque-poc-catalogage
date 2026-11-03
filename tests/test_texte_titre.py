"""Titre de classement (SPEC §4.4)."""

import pytest

from humatheque.texte_unimarc import titre_classement


@pytest.mark.parametrize("titre, langue, attendu", [
    ("L'évolution des pratiques", "fre", "L'@évolution des pratiques"),
    ("L’évolution des pratiques", "fre", "L’@évolution des pratiques"),
    ("Les coraux tempérés", "fre", "Les @coraux tempérés"),
    ("Une histoire", "fre", "Une @histoire"),
    ("Histoire des États-Unis", "fre", "Histoire des États-Unis"),
    ("The making of", "eng", "The @making of"),
    ("Der Prozess", "ger", "Der @Prozess"),
    ("Il nome della rosa", "ita", "Il @nome della rosa"),
    ("L'amica geniale", "ita", "L'@amica geniale"),
    ("El otoño", "spa", "El @otoño"),
    ("Le", "fre", "Le"),                    # article seul : pas de @
    ("Lesbos", "fre", "Lesbos"),            # « Les » n'est pas un mot entier
])
def test_titre_classement(titre, langue, attendu):
    r = titre_classement(titre, langue)
    assert r.valeur == attendu and r.alertes == []


def test_langue_sans_table():
    r = titre_classement("Le titre", "lat")
    assert r.valeur == "Le titre"
    assert r.alertes == ["langue_sans_table_articles:lat"]


def test_langue_absente():
    assert titre_classement("Le titre", None).alertes == ["langue_sans_table_articles:absente"]
