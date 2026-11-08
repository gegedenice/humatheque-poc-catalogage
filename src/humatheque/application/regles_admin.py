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
from humatheque.regles.chargement import FICHIER_DECISIONS, ReglesInvalides, charger_regles, fichiers_regles
from humatheque.regles.ecriture import remplacer_regles
from humatheque.regles.modeles import EtatVerification, Fait, RegleDecision, ServiceSorties
from humatheque.regles.moteur import Choix, choisir, conventions
from humatheque.regles.notice import generer
from humatheque.regles.verificateur import Rapport, verifier, verifier_textes

if TYPE_CHECKING:
    from humatheque.application import Application

EXEMPLE = Path("reference") / "moteur-regles" / "exemple_contexte.json"
JOURNAL = "journal_regles.jsonl"


class ProfilVue(BaseModel):
    profil: str
    libelle: str
    fichier: str
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
    faits: dict[str, Fait]
    services: list[ServiceSorties] = Field(default_factory=list)
    actions: dict[str, dict[str, str]] = Field(default_factory=dict)
    decisions: list[dict[str, Any]]
    sinon: dict[str, Any]
    profils: list[ProfilVue]
    verifications: list[EtatVerification] = Field(default_factory=list)
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
    verifications: list[str] = Field(default_factory=list)
    zones: list[dict[str, Any]] = Field(default_factory=list, description="zone produite, règle, statut")


class BancEssai(BaseModel):
    """Banc d'essai de bout en bout : décision, données préparées, notice."""

    source: str
    choix: Choix
    donnees: list[dict[str, Any]] = Field(default_factory=list, description="variable, source, valeur")
    apercu: Apercu | None = None
    profil_essaye: str | None = Field(None, description="profil de l'aperçu (décision ou choix explicite)")


class EcartNotice(BaseModel):
    dossier: str
    document: str
    profil: str
    zones: list[str] = Field(description="étiquettes dont le contenu change")
    avant: str
    apres: str


class NonRegression(BaseModel):
    version: str
    comparees: int
    ecarts: list[EcartNotice]

    @property
    def zones_touchees(self) -> list[str]:
        return sorted({z for e in self.ecarts for z in e.zones})


class EnregistrementRegles(BaseModel):
    fichier: str
    archive: str
    version: str
    rapport: Rapport


class AdministrationRegles:
    # -------------------------------------------------------------- consultation
    def vue_regles(self: "Application") -> VueRegles:
        r = self.regles
        profils = [ProfilVue(profil=nom, libelle=v.libelle or nom, fichier=v.fichier,
                             decisions=[d.nom for d in r.decisions if d.profil == nom]) for nom, v in r.variantes.items()]
        exemple = self.exemple_variables()
        variables = [{"nom": n, **v.model_dump(exclude={"nom"}), "exemple": _apercu_valeur(exemple.get(n))}
                     for n, v in r.variables.items()]
        actions = conventions()["actions"]
        return VueRegles(version=r.version, emplacement=r.emplacement, rapport=self._rapport, faits=r.faits,
                         services=list(r.services.values()), actions=actions,
                         decisions=[{**d.model_dump(exclude_none=True), "condition": d.texte_condition,
                                     "admises": {f: d.admises(f) for f in r.faits},
                                     "autres": {f: d.admises(f) for f in d.quand if f not in r.faits},
                                     "issue": (actions.get(d.alors) or {}).get("issue")} for d in r.decisions],
                         sinon={**r.sinon, "issue": (actions.get(r.sinon.get("alors")) or {}).get("issue")},
                         profils=profils, verifications=list(r.verifications.values()), variables=variables, fichiers=list(r.textes), journal=self.journal_regles())

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
        """Codes d'API du dossier (à défaut, valeurs des faits), pour le banc d'essai."""
        from humatheque.decision.moteur import faits_du_dossier

        d = self._dossier(dossier_id)
        if d.decision and d.decision.faits:
            return {k: (d.decision.codes.get(k) if d.decision.codes.get(k) not in (None, "")
                        and not c["corrige"] else v)
                    for k, v, c in ((c["fait"], c["valeur"], c) for c in d.decision.chaine(self.regles))}
        return faits_du_dossier(self.regles, d.document.type_document, d.provenance.valeur if d.provenance else None,
                                d.etat_alma, d.etat_sudoc)

    def essayer_decision(self: "Application", faits: dict[str, Any]) -> Choix:
        """Règle retenue pour ces faits, et évaluation de chaque condition. Chaque fait peut
        être donné par sa valeur en clair ou par le code renvoyé par l'API (traduit)."""
        vides = {k: None for k, v in faits.items() if v in (None, "")}
        codes = {k: v for k, v in faits.items() if k not in vides}
        return choisir(self.regles, vides, codes=codes, tout_evaluer=True)

    # -------------------------------------------------------------- tableau de décision (formulaire)
    def regle_decision(self: "Application", nom: str) -> RegleDecision | None:
        return next((r for r in self.regles.decisions if r.nom == nom), None)

    def _ecrire_decisions(self: "Application", regles: list[RegleDecision], sinon: dict[str, Any],
                          auteur: str) -> EnregistrementRegles:
        texte = remplacer_regles(self.texte_regles(FICHIER_DECISIONS), regles, sinon)
        return self.enregistrer_regles(FICHIER_DECISIONS, texte, auteur)

    def enregistrer_decision(self: "Application", ancien_nom: str | None, champs: dict[str, Any], auteur: str,
                             position: int | None = None) -> EnregistrementRegles:
        """Ajoute (`ancien_nom` vide) ou modifie une règle ; « sinon » : action et motif seulement.
        `champs` : nom, quand {fait: [valeurs]}, si, alors, profil, motif, a_valider."""
        regles = list(self.regles.decisions)
        if ancien_nom == "sinon":
            sinon = {"alors": champs.get("alors") or "vérification", "motif": (champs.get("motif") or "").strip(),
                     "verification": champs.get("verification") or ""}
            return self._ecrire_decisions(regles, sinon, auteur)
        quand = {f: (v[0] if len(v) == 1 else v) for f, v in (champs.get("quand") or {}).items() if v}
        nouvelle = RegleDecision(nom=(champs.get("nom") or "").strip(), quand=quand,
                                 alors=champs.get("alors") or "", profil=champs.get("profil") or None,
                                 verification=champs.get("verification") or None,
                                 motif=(champs.get("motif") or "").strip() or None,
                                 a_valider=bool(champs.get("a_valider")))
        if not nouvelle.nom:
            raise ActionImpossible("le nom de la règle est requis")
        if nouvelle.nom == "sinon":
            raise ActionImpossible("« sinon » est réservé à la règle par défaut")
        if self.regles.actions_sans_profil(nouvelle.alors):
            nouvelle.profil = None
        if nouvelle.alors != "vérification":
            nouvelle.verification = None
        index = next((i for i, r in enumerate(regles) if r.nom == ancien_nom), None) if ancien_nom else None
        if ancien_nom and index is None:
            raise Introuvable(f"règle « {ancien_nom} »")
        if index is not None:
            regles[index] = nouvelle
        else:
            regles.insert(len(regles) if position is None else max(0, min(position, len(regles))), nouvelle)
        return self._ecrire_decisions(regles, dict(self.regles.sinon), auteur)

    def supprimer_decision(self: "Application", nom: str, auteur: str) -> EnregistrementRegles:
        regles = [r for r in self.regles.decisions if r.nom != nom]
        if len(regles) == len(self.regles.decisions):
            raise Introuvable(f"règle « {nom} »")
        return self._ecrire_decisions(regles, dict(self.regles.sinon), auteur)

    def deplacer_decision(self: "Application", nom: str, sens: int, auteur: str) -> EnregistrementRegles:
        """Monte (sens -1) ou descend (+1) une règle : l'ordre décide."""
        regles = list(self.regles.decisions)
        i = next((i for i, r in enumerate(regles) if r.nom == nom), None)
        if i is None:
            raise Introuvable(f"règle « {nom} »")
        j = i + (1 if sens > 0 else -1)
        if not 0 <= j < len(regles):
            raise ActionImpossible("la règle est déjà à cette extrémité")
        regles[i], regles[j] = regles[j], regles[i]
        return self._ecrire_decisions(regles, dict(self.regles.sinon), auteur)

    # -------------------------------------------------------------- aperçu d'un profil
    def _variables_essai(self: "Application", dossier_id: str | None) -> tuple[dict[str, Any], str]:
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
            variables = self.exemple_variables()
            if not variables:
                raise ActionImpossible(f"exemple de contexte introuvable ({EXEMPLE}) : choisissez un dossier")
            source = "exemple (reference/moteur-regles/exemple_contexte.json)"
        return variables, source

    def apercu_profil(self: "Application", profil: str, dossier_id: str | None = None) -> Apercu:
        if profil not in self.regles.variantes:
            raise Introuvable(f"profil {profil}")
        variables, source = self._variables_essai(dossier_id)
        n = generer(self.regles, profil, variables)
        return Apercu(profil=profil, source=source, texte=vers_winibw(n), annote=vers_winibw(n, annoter=True),
                      alertes=n.alertes, non_couvertes=[f"{x.zone} [{x.regle}]" for x in n.non_couvertes],
                      variables=variables, verifications=n.verifications,
                      zones=[{"texte": z.texte(), "etiquette": z.etiquette,
                              "regles": [r.id for r in z.regles], "statut": z.regles[0].statut if z.regles else "",
                              "repli": any(r.id in n.replis for r in z.regles)} for z in n.zones_triees()])

    def banc_essai(self: "Application", dossier_id: str | None = None, faits: dict[str, Any] | None = None,
                   profil: str | None = None) -> BancEssai:
        """Décisions appliquées, données préparées et notice prévisualisée, d'un coup."""
        choix = self.essayer_decision(self.faits_dossier(dossier_id) if dossier_id else (faits or {}))
        variables, source = self._variables_essai(dossier_id)
        donnees = [{"nom": n, "source": v.source, "valeur": variables.get(n), "disponible": v.disponible}
                   for n, v in self.regles.variables.items()]
        cible = profil or (choix.profil if choix.suite in ("creer", "convertir") else None)
        apercu = self.apercu_profil(cible, dossier_id) if cible in self.regles.variantes else None
        return BancEssai(source=source, choix=choix, donnees=donnees, apercu=apercu, profil_essaye=cible)

    def non_regression(self: "Application", limite: int = 25) -> NonRegression:
        """Régénère, avec les règles chargées, les notices déjà produites (dossiers terminés) et
        liste les zones qui changeraient : la configuration modifiée est-elle sans surprise ?"""
        from humatheque.contexte import ResultatsServices

        ecarts, comparees = [], 0
        for lot in self.store.lots(50):
            for ligne, d in self.store.dossiers_du_lot(lot.id):
                if comparees >= limite:
                    break
                profil = d.decision.profil_cible if d.decision else None
                if not (d.notice_texte and d.metadonnees and profil in self.regles.variantes):
                    continue
                res = ResultatsServices(personnes=d.idref_personnes, organisations=d.idref_organisations,
                                        dewey_code=d.dewey.code if d.dewey else None)
                variables = variables_profil(construire(d.metadonnees, d.document.type_document, res).valeurs,
                                             self.regles)
                apres = vers_winibw(generer(self.regles, profil, variables))
                comparees += 1
                if apres != d.notice_texte:
                    a, b = d.notice_texte.splitlines(), apres.splitlines()
                    zones = sorted({l[:3] for l in set(a) ^ set(b)})
                    ecarts.append(EcartNotice(dossier=d.id, document=d.document.id, profil=profil, zones=zones,
                                              avant=d.notice_texte, apres=apres))
        return NonRegression(version=self.regles.version, comparees=comparees, ecarts=ecarts)

    def exemple_variables(self: "Application") -> dict[str, Any]:
        """Contexte d'exemple (thèse fictive de la référence), vide s'il est introuvable."""
        candidats = [self.reglages.racine / EXEMPLE, Path(__file__).resolve().parents[3] / EXEMPLE]
        chemin = next((c for c in candidats if c.exists()), None)
        return json.loads(chemin.read_text(encoding="utf-8")) if chemin else {}

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


def _apercu_valeur(v: Any) -> str:
    """Valeur d'exemple lisible : formes des personnes / organismes, listes jointes."""
    if v is None or v == [] or v == "":
        return ""
    if isinstance(v, dict):
        return " · ".join(f"{k} {x}" for k, x in v.items() if x not in (None, ""))
    if isinstance(v, list):
        return " | ".join(_apercu_valeur(x) for x in v)
    return str(v)


class ReglesRefusees(ActionImpossible):
    """Brouillon refusé : le vérificateur signale au moins une erreur bloquante."""

    def __init__(self, rapport: Rapport) -> None:
        self.rapport = rapport
        super().__init__("modification refusée par le vérificateur : "
                         + "; ".join(f"{c.table} {c.ligne} : {c.message}".replace("  ", " ") for c in rapport.erreurs),
                         details=rapport.erreurs)
