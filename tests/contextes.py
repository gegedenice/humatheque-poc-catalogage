"""Contextes écrits à la main pour les tests du générateur (SPEC §11, étape 3).

Données fictives : les PPN sont des valeurs de test, pas des autorités réelles.
"""

from __future__ import annotations

import copy


def _p(forme, nom, prenom, ppn=None, statut="accepted"):
    return {"forme": forme, "nom": nom, "prenom": prenom, "ppn": ppn,
            "statut_idref": statut if ppn else "not_found"}


def _o(forme, ppn=None):
    return {"forme": forme, "ppn": ppn, "statut_idref": "accepted" if ppn else "not_found"}


# Thèse originale soutenue à l'EPHE (profil these_originale_imprimee)
THESE_ORIGINALE_EPHE = {
    "title": "Contribution à l'étude de la démocratie en Afrique",
    "titre_classement": "Contribution à l'étude de la démocratie en Afrique",
    "subtitle": ["le cas du Sénégal", "1960-1990"],
    "author": [_p("Jean-Daniel DUBOIS", "Dubois", "Jean-Daniel", "TEST00001")],
    "degree_type": "Thèse de doctorat",
    "discipline": "Science politique",
    "volume": None,
    "volumaison": "1 volume",
    "granting_institution": _o("École pratique des hautes études", "TEST00100"),
    "co_tutelle_institutions": [],
    "doctoral_school": _o("École doctorale de l'EPHE", None),
    "defense_year": "1998",
    "advisor": [_p("Marie-France de LA ROCHE", "La Roche", "Marie-France de", "TEST00002"),
                _p("Paul MARTIN", "Martin", "Paul")],
    "jury_president": [_p("Anne LEROY", "Leroy", "Anne", "TEST00003")],
    "reviewers": [_p("Luc PETIT", "Petit", "Luc", "TEST00004"),
                  _p("Claire DURAND", "Durand", "Claire")],
    "committee_members": [],
    "language": "fre",
    "abstract": None,
    "code_etab": "EPHE",
    "numero_nnt": "XXXX",
    "dewey_code": "320",
}

# Reproduction d'une thèse soutenue hors EPHE (profil these_reproduction_imprimee)
REPRODUCTION_HORS_EPHE = copy.deepcopy(THESE_ORIGINALE_EPHE) | {
    "title": "Les coraux tempérés",
    "titre_classement": "Les @coraux tempérés",
    "subtitle": [],
    "granting_institution": _o("Université de Montpellier", None),
    "co_tutelle_institutions": [_o("Université de Genève", "TEST00200"), _o("Université de Liège")],
    "doctoral_school": None,
    "advisor": [_p("Paul MARTIN", "Martin", "Paul")],
    "defense_year": "2005",
}
