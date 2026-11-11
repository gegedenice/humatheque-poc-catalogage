"""Adaptateur extraction → Contexte (SPEC §5).

Le contexte est un dict des valeurs normalisées (clés du schéma d'extraction
et valeurs calculées) ; `variables_profil` en tire les variables de
`regles/variables.yaml`, seul contrat lu par le générateur. Chaque
transformation (avant / après, fonction appliquée, alertes) est journalisée.
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Any

from pydantic import BaseModel, Field

from humatheque.config import charger_yaml, reglages
from humatheque.services.idref import AlignementIdref, AlignementOrganisations
from humatheque.texte_unimarc import minuscule_initiale, personne, titre_classement


@lru_cache
def _cfg() -> dict[str, Any]:
    return charger_yaml("contexte")


def _liste(v: Any) -> list[str]:
    """Champ multivalué → liste de chaînes (tolère l'ancienne convention « A | B »)."""
    if v is None or v == "":
        return []
    if isinstance(v, list):
        return [str(x).strip() for x in v if str(x).strip()]
    return [p.strip() for p in str(v).split("|") if p.strip()]


def normaliser_metadonnees(extraction: dict[str, Any]) -> dict[str, Any]:
    """`metadata.normalize_metadata` du POC (schéma v2) + `abstract` : structure seulement."""
    cfg = _cfg()
    m = {champ: extraction.get(champ) for champ in cfg["champs_extraction"]}
    for champ in cfg["multivalues"]:
        m[champ] = _liste(m.get(champ))
    for champ in cfg["personnes"]:
        if champ in cfg["multivalues"]:
            continue
        v = m.get(champ)
        if isinstance(v, list):
            noms = [str(x).strip() for x in v if str(x).strip()]
            m[champ] = noms[0] if noms else None
    for champ, v in list(m.items()):
        if champ not in cfg["multivalues"] and isinstance(v, str):
            m[champ] = v.strip() or None
    if m.get("volume") is not None:
        m["volume"] = str(m["volume"]).strip() or None
    if m.get("defense_year") is not None:
        m["defense_year"] = str(m["defense_year"]).strip() or None
    return m


def document_vide(m: dict[str, Any]) -> bool:
    """Aucune métadonnée du schéma (ING-F-008)."""
    return all(v in (None, "", []) for k, v in m.items() if k != "confidence")


class Transformation(BaseModel):
    champ: str
    fonction: str
    avant: Any
    apres: Any
    alertes: list[str] = Field(default_factory=list)
    trace: list[str] = Field(default_factory=list)


class Contexte(BaseModel):
    valeurs: dict[str, Any]
    alertes: list[str] = Field(default_factory=list)
    transformations: list[Transformation] = Field(default_factory=list)


class ResultatsServices(BaseModel):
    """Résultats disponibles au moment de la construction (contexte partiel sinon)."""

    personnes: dict[str, list[AlignementIdref | None]] = Field(
        default_factory=dict, description="champ → alignement par élément, dans l'ordre")
    organisations: AlignementOrganisations | None = None
    dewey_code: str | None = None


def _ppn(a: AlignementIdref | None) -> tuple[str | None, str | None]:
    if a is None:
        return None, None
    return (a.ppn if a.statut in _cfg()["statuts_idref_ppn"] else None), a.statut


def construire(metadonnees: dict[str, Any], type_document: str,
               resultats: ResultatsServices | None = None) -> Contexte:
    cfg = _cfg()
    res = resultats or ResultatsServices()
    m = metadonnees
    ctx: dict[str, Any] = {}
    alertes: list[str] = []
    journal: list[Transformation] = []

    def noter(champ: str, fonction: str, avant: Any, r: Any) -> Any:
        journal.append(Transformation(champ=champ, fonction=fonction, avant=avant,
                                      apres=getattr(r, "valeur", r), alertes=getattr(r, "alertes", []),
                                      trace=getattr(r, "trace", [])))
        for a in getattr(r, "alertes", []):
            alertes.append(f"{champ}:{a}")
        return getattr(r, "valeur", r)

    # --- Personnes : listes de {forme, nom, prenom, ppn, statut_idref}
    for champ in cfg["personnes"]:
        alignements = res.personnes.get(champ, [])
        elements = []
        for i, brut in enumerate(_liste(m.get(champ))):
            p = personne(brut)
            journal.append(Transformation(champ=f"{champ}[{i + 1}]", fonction="personne", avant=brut,
                                          apres={"nom": p.nom, "prenom": p.prenom}, alertes=p.alertes,
                                          trace=p.trace))
            ppn, statut = _ppn(alignements[i] if i < len(alignements) else None)
            elements.append({"forme": p.forme, "nom": p.nom or None, "prenom": p.prenom or None,
                             "ppn": ppn, "statut_idref": statut})
        ctx[champ] = elements

    # --- Organismes : {forme, ppn, statut_idref}
    orgs = res.organisations or AlignementOrganisations()

    def organisme(champ: str, brut: str, alignement: AlignementIdref | None) -> dict[str, Any]:
        ppn, statut = _ppn(alignement)
        return {"forme": brut, "ppn": ppn, "statut_idref": statut}

    g = m.get("granting_institution")
    ctx["granting_institution"] = organisme("granting_institution", g, orgs.granting_institution) if g else None
    d = m.get("doctoral_school")
    ctx["doctoral_school"] = organisme("doctoral_school", d, orgs.doctoral_school) if d else None
    ctx["co_tutelle_institutions"] = [
        organisme(f"co_tutelle_institutions[{i + 1}]", c,
                  orgs.co_tutelle_institutions[i] if i < len(orgs.co_tutelle_institutions) else None)
        for i, c in enumerate(_liste(m.get("co_tutelle_institutions")))]

    # --- Champs simples
    # Casse et accents : par le VLM (prompt) ; seuls le @ du titre et la minuscule
    # initiale du sous-titre sont faits ici (décision du 11/11).
    ctx["title"] = m.get("title")
    st = m.get("subtitle")
    ctx["subtitle"] = [noter("subtitle", "minuscule_initiale", s, minuscule_initiale(s)) for s in _liste(st)] if st else []
    for champ in ("discipline", "degree_type", "language", "abstract", "volume"):
        ctx[champ] = m.get(champ)
    annee = m.get("defense_year")
    if annee and not re.fullmatch(r"\d{4}", str(annee)):
        alertes.append(f"defense_year:annee_invalide:{annee}")
        annee = None
    ctx["defense_year"] = annee

    # --- Valeurs calculées
    ctx["titre_classement"] = (noter("titre_classement", "titre_classement", ctx["title"],
                                     titre_classement(ctx["title"], ctx["language"])) if ctx["title"] else None)
    vol = cfg["volumaison"]
    ctx["volumaison"] = vol["sans_volume"] if ctx["volume"] is None else vol["avec_volume"]
    ctx["code_etab"] = reglages().code_etab
    ctx["dewey"] = res.dewey_code

    # --- Caractère $ dans une valeur (casserait le format WinIBW)
    for champ, chaine in _chaines(ctx):
        if "$" in chaine:
            alertes.append(f"dollar_dans_valeur:{champ}")
    return Contexte(valeurs=ctx, alertes=list(dict.fromkeys(alertes)), transformations=journal)


def variables_profil(valeurs: dict[str, Any], regles: Any) -> dict[str, Any]:
    """Valeurs du contexte → variables de `regles/variables.yaml` (SPEC §5).

    `source: extraction.<champ>` lit le champ du schéma ; une autre source lit la
    valeur du même nom. Une personne ou un organisme seul (`liste: false`) reçoit
    le premier élément ; une variable `disponible: false` n'est jamais fournie.
    """
    prefixe = "extraction."
    sortie: dict[str, Any] = {}
    for nom, v in regles.variables.items():
        if not v.disponible:
            continue
        cle = v.source[len(prefixe):] if v.source.startswith(prefixe) else nom
        val = valeurs.get(cle)
        if not v.liste and isinstance(val, list):
            val = val[0] if val else None
        elif v.liste and val is not None and not isinstance(val, list):
            val = [val]
        sortie[nom] = val
    return sortie


def _chaines(v: Any, chemin: str = "") -> list[tuple[str, str]]:
    if isinstance(v, str):
        return [(chemin, v)]
    if isinstance(v, dict):
        return [x for k, e in v.items() for x in _chaines(e, f"{chemin}.{k}" if chemin else k)]
    if isinstance(v, list):
        return [x for i, e in enumerate(v) for x in _chaines(e, f"{chemin}[{i + 1}]")]
    return []
