"""Moteur de décision (SPEC §7).

- `faits_du_dossier` : type, provenance, alma, sudoc, traduits par le dictionnaire
  des faits (`regles/decisions.yaml`) ;
- `decider` : faits → première règle de `regles/decisions.yaml` dont la condition
  rule-engine est vraie (sinon `sinon`) → suite du traitement (créer, convertir,
  arrêter) et issue (terminé, à reprendre) ;
- `routage_service` : `(service, code)` → issue par `regles/services.yaml` ;
  alma et sudoc sont jugés par leurs faits et `decisions.yaml` ;
- `routage_final` : l'issue la plus sévère des étapes (`humatheque.issues`).

Aucune décision ni aucune issue de service n'est écrite ici : tout vient de
`regles/`.
"""

from __future__ import annotations

from collections.abc import Iterable
from functools import lru_cache
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from humatheque import issues
from humatheque.config import charger_yaml
from humatheque.regles.modeles import Regles
from humatheque.regles.moteur import choisir, traduire

Suite = Literal["creer", "convertir", "arreter"]


@lru_cache
def _cfg() -> dict[str, Any]:
    return charger_yaml("decision")


# Identifiants des règles avant le 06/11 (hérités de Grist) → nom en clair, pour
# relire les dossiers enregistrés ; les nouvelles décisions portent déjà le nom.
ANCIENS_NOMS = {
    "S01": "Notice déjà dans Alma", "S14": "Candidat Alma à vérifier", "P01": "Provenance à confirmer",
    "S10": "Résultat Sudoc à vérifier", "S02": "Thèse hors EPHE, originale imprimée au Sudoc",
    "S03": "Thèse hors EPHE, notice électronique au Sudoc",
    "S04": "Thèse hors EPHE, reproduction imprimée déjà au Sudoc",
    "S05": "Thèse hors EPHE, reproduction microfiche au Sudoc", "S06": "Thèse EPHE, notice électronique au Sudoc",
    "S07": "Thèse EPHE, reproduction au Sudoc", "S08": "Thèse EPHE absente du Sudoc",
    "S09": "Thèse hors EPHE absente du Sudoc", "S12": "Mémoire absent du Sudoc",
    "S13": "Mémoire, notice électronique au Sudoc",
}


class Decision(BaseModel):
    faits: dict[str, Any] = Field(default_factory=dict)
    codes: dict[str, Any] = Field(default_factory=dict, description="codes d'API à l'origine des faits")
    situation: str | None = Field(None, description="nom de la règle de decisions.yaml retenue (« sinon » à défaut)")
    ligne: dict[str, Any] | None = Field(None, description="règle retenue, pour affichage")
    action: str | None = None
    suite: Suite
    profil_cible: str | None = None
    routage: str
    motif: str

    @field_validator("situation", mode="before")
    @classmethod
    def _ancien_nom(cls, v: Any) -> Any:
        return ANCIENS_NOMS.get(v, v) if isinstance(v, str) else v

    def texte_faits(self) -> str:
        return ", ".join(f"{k} = {v}" for k, v in self.faits.items())

    def chaine(self, regles: Regles | None = None) -> list[dict[str, Any]]:
        """Fait par fait : code de l'API → valeur retenue (affichage du dossier) ;
        `corrige` : valeur différente de la traduction du code (choix d'un catalogueur)."""
        sortie = []
        for k, v in self.faits.items():
            code = self.codes.get(k)
            f = regles.faits.get(k) if regles else None
            corrige = bool(f and code not in (None, "") and f.traduire(code) != v)
            sortie.append({"fait": k, "code": code, "valeur": v, "corrige": corrige})
        return sortie


class RoutageService(BaseModel):
    service: str
    statut: str
    libelle: str | None = Field(None, description="sortie en clair (regles/services.yaml)")
    routage: str | None = Field(None, description="issue ; None : jugé par les états (alma, sudoc)")
    bloquant: bool = False
    exception: bool = False
    alertes: list[str] = Field(default_factory=list)
    motif: str = ""


def faits_du_dossier(regles: Regles, type_document: str, provenance: str | None, etat_alma: str | None,
                     etat_sudoc: str | None) -> dict[str, Any]:
    """Faits de décision (§7), traduits par le dictionnaire des faits (`decisions.yaml`)."""
    bruts = {"type": type_document, "provenance": provenance, "alma": etat_alma, "sudoc": etat_sudoc}
    return {t.fait: t.valeur if t.valeur is not None else bruts[t.fait]
            for t in traduire(regles, {k: v for k, v in bruts.items()})}


def decider(regles: Regles, faits: dict[str, Any], codes: dict[str, Any] | None = None) -> Decision:
    c = choisir(regles, faits)
    nom = c.regle.get("nom")
    if nom == "sinon":
        motif = f"{c.motif} ({', '.join(f'{k} = {v}' for k, v in faits.items())})"
    else:
        motif = nom + (f" : {c.regle['motif']}" if c.regle.get("motif") and c.regle["motif"] != nom else "")
    return Decision(faits=faits, codes=dict(codes or {}), situation=nom, ligne=c.regle, action=c.action, suite=c.suite,
                    profil_cible=c.profil if c.suite in ("creer", "convertir") else None,
                    routage=c.issue, motif=motif)


def routage_service(regles: Regles, service: str, statut: str) -> RoutageService:
    """Issue d'une réponse de service (`regles/services.yaml`, clé du service : extraction,
    idref_personne, idref_organisations, dewey). Alma et sudoc : jugés par leurs faits."""
    if service in regles.faits:
        return RoutageService(service=service, statut=statut, libelle=regles.faits[service].traduire(statut),
                              motif="jugé par le dictionnaire des faits et decisions.yaml")
    cfg = regles.services.get(service)
    sortie = cfg.sortie(statut) if cfg else None
    if sortie is None or sortie.issue not in issues.ORDRE:
        return RoutageService(service=service, statut=statut, routage=issues.INCIDENT, bloquant=True,
                              motif=f"{_cfg()['statut_inconnu']['motif']} : {service} / {statut}")
    a_reprendre = sortie.issue == issues.A_REPRENDRE
    bloquant = sortie.issue == issues.INCIDENT or (a_reprendre and cfg.bloquant)
    return RoutageService(service=service, statut=statut, libelle=sortie.libelle, routage=sortie.issue,
                          bloquant=bloquant, exception=a_reprendre, alertes=[sortie.alerte] if sortie.alerte else [],
                          motif=f"{cfg.libelle or service} : {sortie.libelle}")


def routage_final(routages: Iterable[str | None]) -> str:
    """Issue du dossier : la plus sévère de ses étapes."""
    return issues.la_plus_severe(routages)
