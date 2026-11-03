"""Reprises manuelles d'un dossier arrêté (SPEC §6, §9.3). Chaque résolution
est journalisée (qui / quand / quoi) ; le pipeline reprend ensuite par
`Workflow.executer`."""

from __future__ import annotations

from humatheque.config import charger_yaml
from humatheque.workflow.modeles import Dossier


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
