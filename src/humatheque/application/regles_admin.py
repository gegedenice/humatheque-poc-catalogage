"""Administration des règles (SPEC §9.3, page « Règles ») : consultation, banc
d'essai des décisions, aperçu d'un profil, modification des fichiers YAML.

Une modification est vérifiée avant écriture (aucune erreur bloquante
acceptée), la version précédente est archivée dans
`regles/archives/<horodatage>/`, l'action est journalisée (qui, quand, quoi)
dans `data/journal_regles.jsonl`, puis les règles sont rechargées.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from humatheque.application import ActionImpossible, Introuvable  # définies avant l'import de ce module
from humatheque.contexte import construire, variables_profil
from humatheque.generateur import vers_winibw
from humatheque.regles.chargement import ReglesInvalides, charger_regles, fichiers_regles
from humatheque.regles.moteur import Choix, choisir, conventions, generer, productible, variable_iteree
from humatheque.regles.verificateur import Rapport, verifier, verifier_textes

if TYPE_CHECKING:
    from humatheque.application import Application

EXEMPLE = Path("reference") / "moteur-regles" / "exemple_contexte.json"
JOURNAL = "journal_regles.jsonl"


class LigneProfilVue(BaseModel):
    numero: int
    ligne: str
    etiquette: str
    a_valider: bool
    priorite: int | None
    remarque: str | None
    si: str | None
    joindre: dict[str, list[str]]
    actif: bool
    productible: bool
    repetee_sur: str | None = Field(None, description="variable personne / organisme : une zone par élément")


class ProfilVue(BaseModel):
    profil: str
    libelle: str
    fichier: str
    variables: list[str]
    lignes: list[LigneProfilVue]
    decisions: list[str] = Field(default_factory=list, description="règles de décision qui mènent à ce profil")


class EntreeJournalRegles(BaseModel):
    le: datetime
    auteur: str
    fichier: str
    archive: str


class VueRegles(BaseModel):
    version: str
    emplacement: str
    rapport: Rapport
    faits: dict[str, list[str]]
    decisions: list[dict[str, Any]]
    sinon: dict[str, Any]
    profils: list[ProfilVue]
    variables: list[dict[str, Any]]
    fichiers: list[str]
    journal: list[EntreeJournalRegles] = Field(default_factory=list)


class Apercu(BaseModel):
    profil: str
    source: str = Field(description="contexte utilisé : dossier ou exemple")
    texte: str
    annote: str
    alertes: list[str]
    non_couvertes: list[str]
    variables: dict[str, Any]


class EnregistrementRegles(BaseModel):
    fichier: str
    archive: str
    version: str
    rapport: Rapport


class AdministrationRegles:
    # -------------------------------------------------------------- consultation
    def vue_regles(self: "Application") -> VueRegles:
        r = self.regles
        profils = []
        for nom, p in r.profils.items():
            lignes = [LigneProfilVue(numero=l.numero, ligne=l.ligne, etiquette=l.etiquette,
                                     a_valider=l.a_valider, priorite=l.priorite, remarque=l.remarque, si=l.si,
                                     joindre=l.joindre, actif=l.actif, productible=productible(r, l),
                                     repetee_sur=variable_iteree(r, l)) for l in p.lignes]
            profils.append(ProfilVue(profil=nom, libelle=p.libelle, fichier=p.fichier, variables=p.variables,
                                     lignes=lignes, decisions=[d.nom for d in r.decisions if d.profil == nom]))
        variables = [{"nom": n, **v.model_dump(exclude={"nom"})} for n, v in r.variables.items()]
        actions = conventions()["actions"]
        return VueRegles(version=r.version, emplacement=r.emplacement, rapport=self._rapport, faits=r.faits,
                         decisions=[{**d.model_dump(exclude_none=True),
                                     "issue": (actions.get(d.alors) or {}).get("issue")} for d in r.decisions],
                         sinon={**r.sinon, "issue": (actions.get(r.sinon.get("alors")) or {}).get("issue")},
                         profils=profils, variables=variables, fichiers=list(r.textes), journal=self.journal_regles())

    def fichiers_regles(self: "Application") -> dict[str, str]:
        """Textes des fichiers de règles tels que chargés (API GET /api/v1/regles)."""
        return dict(self.regles.textes)

    def dossiers_pour_essai(self: "Application", limite: int = 30) -> list[dict[str, str]]:
        """Derniers dossiers terminés avec métadonnées (choix du banc d'essai et de l'aperçu)."""
        sortie = []
        for lot in self.store.lots(20):
            for ligne, d in self.store.dossiers_du_lot(lot.id):
                if ligne.etat == "termine" and d.metadonnees:
                    titre = (d.metadonnees or {}).get("title") or ""
                    sortie.append({"id": d.id, "document": d.document.id, "titre": str(titre)[:60],
                                   "regle": (d.decision.situation if d.decision else "") or ""})
                if len(sortie) >= limite:
                    return sortie
        return sortie

    # -------------------------------------------------------------- banc d'essai
    def faits_dossier(self: "Application", dossier_id: str) -> dict[str, Any]:
        from humatheque.decision.moteur import faits_du_dossier

        d = self._dossier(dossier_id)
        if d.decision and d.decision.faits:
            return dict(d.decision.faits)
        return faits_du_dossier(d.document.type_document, d.provenance.valeur if d.provenance else None,
                                d.etat_alma, d.etat_sudoc)

    def essayer_decision(self: "Application", faits: dict[str, Any]) -> Choix:
        """Règle retenue pour ces faits, et évaluation de chaque condition."""
        return choisir(self.regles, {k: (v if v != "" else None) for k, v in faits.items()}, tout_evaluer=True)

    # -------------------------------------------------------------- aperçu d'un profil
    def apercu_profil(self: "Application", profil: str, dossier_id: str | None = None) -> Apercu:
        if profil not in self.regles.profils:
            raise Introuvable(f"profil {profil}")
        if dossier_id:
            d = self._dossier(dossier_id)
            if not d.metadonnees:
                raise ActionImpossible(f"dossier {dossier_id} sans métadonnées extraites")
            from humatheque.contexte import ResultatsServices

            res = ResultatsServices(personnes=d.idref_personnes, organisations=d.idref_organisations,
                                    dewey_code=d.dewey.code if d.dewey else None)
            variables = variables_profil(construire(d.metadonnees, d.document.type_document, res).valeurs,
                                         self.regles)
            source = f"dossier {d.document.id}"
        else:
            candidats = [self.reglages.racine / EXEMPLE, Path(__file__).resolve().parents[3] / EXEMPLE]
            chemin = next((c for c in candidats if c.exists()), None)
            if chemin is None:
                raise ActionImpossible(f"exemple de contexte introuvable ({EXEMPLE}) : choisissez un dossier")
            variables = json.loads(chemin.read_text(encoding="utf-8"))
            source = "exemple (reference/moteur-regles/exemple_contexte.json)"
        n = generer(self.regles, profil, variables)
        return Apercu(profil=profil, source=source, texte=vers_winibw(n), annote=vers_winibw(n, annoter=True),
                      alertes=n.alertes, non_couvertes=[f"{x.zone} [{x.regle}]" for x in n.non_couvertes],
                      variables=variables)

    # -------------------------------------------------------------- modification
    def _fichier_autorise(self: "Application", fichier: str) -> Path:
        dossier = self.reglages.dossier_regles
        if fichier not in fichiers_regles(dossier):
            raise ActionImpossible(f"fichier de règles inconnu : {fichier}")
        return dossier / fichier

    def texte_regles(self: "Application", fichier: str) -> str:
        return self._fichier_autorise(fichier).read_text(encoding="utf-8")

    def verifier_brouillon(self: "Application", fichier: str, texte: str) -> Rapport:
        self._fichier_autorise(fichier)
        return verifier_textes({fichier: texte}, self.reglages)

    def enregistrer_regles(self: "Application", fichier: str, texte: str, auteur: str) -> EnregistrementRegles:
        chemin = self._fichier_autorise(fichier)
        if not auteur.strip():
            raise ActionImpossible("le nom du catalogueur est requis")
        rapport = self.verifier_brouillon(fichier, texte)
        if rapport.bloquant:
            raise ReglesRefusees(rapport)
        ancien = chemin.read_text(encoding="utf-8")
        if ancien == texte:
            raise ActionImpossible("aucune modification à enregistrer")
        horodatage = datetime.now(UTC)
        archive = self.reglages.dossier_regles / "archives" / horodatage.strftime("%Y%m%dT%H%M%S%fZ") / fichier
        archive.parent.mkdir(parents=True, exist_ok=True)
        archive.write_text(ancien, encoding="utf-8")
        chemin.write_text(texte, encoding="utf-8")
        try:
            regles = charger_regles(self.reglages)
        except ReglesInvalides as e:  # ne devrait pas arriver (vérifié avant) : on restaure
            chemin.write_text(ancien, encoding="utf-8")
            raise ActionImpossible(f"règles illisibles après écriture, fichier restauré : {e}") from e
        with self._verrou:
            self.regles = regles
            self._rapport = verifier(regles)
        rel_archive = archive.relative_to(self.reglages.dossier_regles).as_posix()
        journal = self.reglages.dossier_data / JOURNAL
        journal.parent.mkdir(parents=True, exist_ok=True)
        with journal.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"le": horodatage.isoformat(), "auteur": auteur.strip(), "fichier": fichier,
                                "archive": rel_archive}, ensure_ascii=False) + "\n")
        return EnregistrementRegles(fichier=fichier, archive=rel_archive, version=regles.version,
                                    rapport=self._rapport)

    def journal_regles(self: "Application", limite: int = 20) -> list[EntreeJournalRegles]:
        chemin = self.reglages.dossier_data / JOURNAL
        if not chemin.exists():
            return []
        lignes = [l for l in chemin.read_text(encoding="utf-8").splitlines() if l.strip()]
        return [EntreeJournalRegles.model_validate_json(l) for l in reversed(lignes[-limite:])]


class ReglesRefusees(ActionImpossible):
    """Brouillon refusé : le vérificateur signale au moins une erreur bloquante."""

    def __init__(self, rapport: Rapport) -> None:
        self.rapport = rapport
        super().__init__("modification refusée par le vérificateur : "
                         + "; ".join(f"{c.table} {c.ligne} : {c.message}".replace("  ", " ") for c in rapport.erreurs),
                         details=rapport.erreurs)
