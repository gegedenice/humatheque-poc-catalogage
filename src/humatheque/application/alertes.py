"""Alertes d'un dossier, regroupées par famille pour l'affichage (SPEC §9).

Le code reconnaît la famille d'une alerte (motifs ci-dessous, techniques) ;
`regles/alertes.yaml` (studio › Alertes) dit, par famille, le libellé affiché et
si elle est montrée. Les alertes restent toutes dans le dossier (journal, CSV).
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field

from humatheque.regles.modeles import FamilleAlerte

# Rôles lisibles pour les objets d'alerte (« reviewers[2] Jean Morel » → « Jean Morel (rapporteur) »)
ROLES = {"author": "auteur", "advisor": "directeur", "jury_president": "président du jury",
         "reviewers": "rapporteur", "committee_members": "membre du jury",
         "granting_institution": "établissement", "doctoral_school": "école doctorale",
         "co_tutelle_institutions": "cotutelle", "classe Dewey": "Dewey"}

# (famille, motif) dans l'ordre : la première qui reconnaît l'alerte l'emporte.
FAMILLES: list[tuple[str, re.Pattern]] = [
    ("pipeline", re.compile(r"^pipeline:(?P<etape>\w+):(?P<texte>[^|]*)\|(?P<detail>.*)$")),
    ("anciennes", re.compile(r"proto-notice|:(accent_\w+|mot_inconnu|particule_a_verifier|decoupage_heuristique"
                             r"|bouchon|personne_vide)(:|$)")),
    ("pipeline", re.compile(r"^(?P<detail>.+) : (?P<texte>non trouvé dans IdRef)$")),  # dossiers du 09/11
    ("zone_obligatoire", re.compile(r"^zone (?P<detail>\d{3}) obligatoire non produite")),
    ("conversion_non_implementee", re.compile(r"^conversion_non_implementee$")),
    ("annee_invalide", re.compile(r"^defense_year:annee_invalide:(?P<detail>.*)$")),
    ("caractere_dollar", re.compile(r"^dollar_dans_valeur:(?P<detail>.*)$")),
    ("extraction_divergente", re.compile(r"^extraction_divergente:(?P<detail>.*)$")),
    ("document_invalide", re.compile(r"^document_invalide:(?P<detail>.*)$")),
    ("langue_sans_articles", re.compile(r"langue_sans_table_articles:(?P<detail>.*)$")),
]
EXEMPLES = {
    "pipeline": "Non trouvé dans IdRef : Ivar Ekeland (président du jury), Jean-Michel Morel (rapporteur)",
    "zone_obligatoire": "029",
    "conversion_non_implementee": "",
    "annee_invalide": "vers 1998",
    "caractere_dollar": "title",
    "extraction_divergente": "defense_year",
    "document_invalide": "titre non extrait",
    "langue_sans_articles": "lat",
    "anciennes": "granting_institution:accent_ambigu:PARIS · numero_nnt:bouchon · … : proto-notice d'autorité à créer",
    "autres": "toute alerte non reconnue",
}


class GroupeAlertes(BaseModel):
    famille: str
    libelle: str
    elements: list[str] = Field(default_factory=list)


def famille(alerte: str) -> tuple[str, dict[str, str]]:
    for code, motif in FAMILLES:
        m = motif.search(alerte)
        if m:
            return code, {k: v for k, v in m.groupdict().items() if v is not None}
    return "autres", {}


def _objet_lisible(objet: str) -> str:
    m = re.match(r"^(\w+)(?:\[\d+\])? (.+)$", objet)
    if m and m[1] in ROLES:
        return f"{m[2]} ({ROLES[m[1]]})"
    return ROLES.get(objet, objet)


def regrouper(alertes: list[str], familles: dict[str, FamilleAlerte]) -> list[GroupeAlertes]:
    """Alertes à afficher, regroupées (une famille masquée n'apparaît pas)."""
    groupes: dict[str, GroupeAlertes] = {}
    for a in alertes:
        code, parties = famille(a)
        f = familles.get(code) or FamilleAlerte(libelle=code)
        if not f.afficher:
            continue
        cle, libelle = code, f.libelle
        if code == "pipeline":  # un groupe par texte d'alerte du pipeline
            texte = (parties.get("texte") or "").strip()
            cle, libelle = f"pipeline:{texte}", (texte[:1].upper() + texte[1:]) or f.libelle
        detail = _objet_lisible(parties["detail"].strip()) if parties.get("detail") else ("" if code != "autres" else a)
        g = groupes.setdefault(cle, GroupeAlertes(famille=code, libelle=libelle))
        if detail and detail not in g.elements:
            g.elements.append(detail)
    return list(groupes.values())


def familles_depuis(donnees: Any) -> dict[str, FamilleAlerte]:
    brutes = (donnees or {}).get("familles") or {}
    return {str(k): FamilleAlerte.model_validate(v or {"libelle": str(k)}) for k, v in brutes.items()}
