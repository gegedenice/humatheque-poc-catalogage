"""Reprises manuelles d'un dossier arrêté (SPEC §6, §9.3). Chaque résolution
est journalisée (qui / quand / quoi) ; le pipeline reprend ensuite par
`Workflow.executer`."""

from __future__ import annotations

import re

from humatheque.config import charger_yaml
from humatheque.services.idref import AlignementIdref, AlignementOrganisations
from humatheque.workflow.modeles import Cloture, Dossier, Etape

# Statut d'un alignement ou d'une classe tranchés par un catalogueur
# (config/contexte.yaml : statuts_idref_ppn).
MANUEL = "validé manuellement"


class ResolutionImpossible(Exception):
    pass


def _resoudre(d: Dossier, etape: str) -> None:
    for e in d.exceptions:
        if e.etape == etape:
            e.resolue = True


def choisir_provenance(d: Dossier, valeur: str, auteur: str, commentaire: str = "") -> Dossier:
    """Provenance absente ou ambiguë : le catalogueur choisit EPHE ou hors EPHE ;
    le pipeline reprend à l'étape 3 (alma)."""
    cfg = charger_yaml("provenance")["valeurs"]
    if valeur not in (cfg["ephe"], cfg["hors_ephe"]):
        raise ResolutionImpossible(f"provenance « {valeur} » : choisir {cfg['ephe']} ou {cfg['hors_ephe']}")
    _resoudre(d, "provenance")
    d.provenance_manuelle = valeur
    d.noter("provenance", "resolution", {"provenance": valeur, "commentaire": commentaire}, auteur)
    d.reinitialiser_depuis("provenance")
    return d


def declarer_faux_positif(d: Dossier, service: str, commentaire: str, auteur: str) -> Dossier:
    """Vérification Alma ou Sudoc jugée non pertinente (ALM-SUD-006) : état corrigé
    (`absent` pour Alma, `rien` pour Sudoc) puis reprise à l'étape suivante."""
    if service == "alma":
        if not d.alma or d.etat_alma in (None, "absent"):
            raise ResolutionImpossible("aucune notice Alma trouvée : rien à déclarer")
        corrige, reprise = "absent", "sudoc"
    elif service == "sudoc":
        if not d.sudoc or d.etat_sudoc in (None, charger_yaml("etats_sudoc")["etat_rien"]):
            raise ResolutionImpossible("aucun candidat Sudoc retenu : rien à déclarer")
        corrige, reprise = charger_yaml("etats_sudoc")["etat_rien"], "situation"
    else:
        raise ResolutionImpossible(f"service « {service} » : alma ou sudoc attendu")
    if not commentaire.strip():
        raise ResolutionImpossible("un commentaire est requis")
    _resoudre(d, service)
    avant = d.etat_alma if service == "alma" else d.etat_sudoc
    d.corrections[service] = corrige
    if service == "alma":
        d.etat_alma = corrige
    else:
        d.etat_sudoc = corrige
    d.noter(service, "resolution", {"faux_positif": True, "avant": avant, "apres": corrige,
                                    "commentaire": commentaire}, auteur)
    d.reinitialiser_depuis(reprise)
    etape = d.etapes[service]
    if etape.statut == "arret":  # arrêt sur la vérification elle-même : pas de nouvel appel
        etape.statut, etape.routage, etape.motif = "ok", "auto", f"faux positif déclaré : {commentaire}"
    d.decision = None
    return d


def _exception(d: Dossier, index: int, etape: str):
    if not 0 <= index < len(d.exceptions) or d.exceptions[index].etape != etape:
        raise ResolutionImpossible(f"exception {index} introuvable pour l'étape {etape}")
    e = d.exceptions[index]
    if e.resolue:
        raise ResolutionImpossible("exception déjà résolue")
    return e


def _verifier_ppn(ppn: str) -> str:
    ppn = (ppn or "").strip().upper()
    if not re.fullmatch(r"\d{8}[\dX]", ppn):
        raise ResolutionImpossible(f"PPN « {ppn} » mal formé (8 chiffres + chiffre ou X)")
    return ppn


def choisir_ppn(d: Dossier, index: int, ppn: str | None, auteur: str) -> Dossier:
    """Exception IdRef : PPN candidat ou saisi (None = sans lien) ; la notice est
    ensuite régénérée."""
    etape = d.exceptions[index].etape if 0 <= index < len(d.exceptions) else ""
    if etape not in ("idref_personnes", "idref_organisations"):
        raise ResolutionImpossible("cette exception ne concerne pas IdRef")
    e = _exception(d, index, etape)
    ppn = _verifier_ppn(ppn) if ppn else None
    statut = MANUEL if ppn else "sans lien"
    alignement = AlignementIdref(statut=statut, ppn=ppn)
    champ, i = _champ_index(e.objet)
    if e.etape == "idref_personnes":
        d.idref_personnes.setdefault(champ, [])
        while len(d.idref_personnes[champ]) <= i:
            d.idref_personnes[champ].append(None)
        d.idref_personnes[champ][i] = alignement
    else:
        orgs = d.idref_organisations or AlignementOrganisations()
        if champ == "co_tutelle_institutions":
            while len(orgs.co_tutelle_institutions) <= i:
                orgs.co_tutelle_institutions.append(AlignementIdref(statut="not_found"))
            orgs.co_tutelle_institutions[i] = alignement
        else:
            setattr(orgs, champ, alignement)
        d.idref_organisations = orgs
    e.resolue = True
    d.noter(e.etape, "resolution", {"objet": e.objet, "ppn": ppn, "statut": statut}, auteur)
    _regenerer(d)
    return d


def choisir_dewey(d: Dossier, index: int, code: str, auteur: str) -> Dossier:
    """Exception Dewey : classe choisie parmi les candidates ; la notice est régénérée."""
    e = _exception(d, index, "dewey")
    if not d.dewey:
        raise ResolutionImpossible("aucun résultat Dewey")
    classe = next((c for c in d.dewey.classes if c.code == code), None)
    if classe is None:
        raise ResolutionImpossible(f"classe « {code} » absente des propositions")
    d.dewey = d.dewey.model_copy(update={"statut": MANUEL, "code": classe.code, "libelle": classe.libelle,
                                         "score": classe.score})
    e.resolue = True
    d.noter("dewey", "resolution", {"code": classe.code, "libelle": classe.libelle}, auteur)
    _regenerer(d)
    return d


def traiter_hors_outil(d: Dossier, commentaire: str, auteur: str) -> Dossier:
    """Situation tranchée hors de l'outil : le dossier sort de la file d'exceptions."""
    if not commentaire.strip():
        raise ResolutionImpossible("un commentaire est requis")
    for e in d.exceptions:
        e.resolue = True
    d.cloture = Cloture(par=auteur, commentaire=commentaire)
    d.noter(d.etape_arret or "dossier", "resolution", {"traite_hors_outil": True, "commentaire": commentaire}, auteur)
    return d


def _champ_index(objet: str) -> tuple[str, int]:
    """« reviewers[2] Luc PETIT » → ("reviewers", 1) ; « granting_institution X » → (…, 0)."""
    m = re.match(r"(\w+)(?:\[(\d+)\])?", objet)
    if not m:
        raise ResolutionImpossible(f"objet d'exception illisible : {objet}")
    return m[1], int(m[2]) - 1 if m[2] else 0


def _regenerer(d: Dossier) -> None:
    """Remet la génération à faire si la notice a déjà été produite (ou était attendue)."""
    if d.decision and d.decision.suite == "creer" and d.etapes["generation"].statut != "a_faire":
        d.etapes["generation"] = Etape(nom="generation")
