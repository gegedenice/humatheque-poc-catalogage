"""Administration des règles (SPEC §9.3, studio) : pipeline (arbre de décision par
étape, édité nœud par nœud), banc d'essai, aperçu d'une notice, fichiers YAML.

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
import yaml

from humatheque.regles.chargement import FICHIER_PIPELINE, ReglesInvalides, charger_regles, fichiers_regles
from humatheque.regles.modeles import RESULTATS, EtapePipeline, Noeud
from humatheque.regles.moteur import Issue, evaluer
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
    communes: dict[str, list[str]]
    etapes: list[EtapePipeline]
    resultats: dict[str, str]
    profils: list[ProfilVue]
    variables: list[dict[str, Any]]
    fichiers: list[str]
    journal: list[EntreeJournalRegles] = Field(default_factory=list)


class Parcours(BaseModel):
    """Banc d'essai : résultat de chaque étape, jusqu'au premier arrêt."""

    etapes: list[Issue]
    notice: str | None = Field(None, description="notice produite ou convertie")
    resultat: str = ""
    issue: str = ""


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
    """Banc d'essai de bout en bout : parcours du pipeline, données préparées, notice."""

    source: str
    parcours: Parcours
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
        atteintes: dict[str, list[str]] = {}
        for e in r.etapes.values():
            for n in _feuilles(e.branches):
                if n.notice:
                    atteintes.setdefault(n.notice, []).append(e.libelle)
        profils = [ProfilVue(profil=nom, libelle=v.libelle or nom, fichier=v.fichier,
                             decisions=sorted(set(atteintes.get(nom, [])))) for nom, v in r.variantes.items()]
        exemple = self.exemple_variables()
        variables = [{"nom": n, **v.model_dump(exclude={"nom"}), "exemple": _apercu_valeur(exemple.get(n))}
                     for n, v in r.variables.items()]
        return VueRegles(version=r.version, emplacement=r.emplacement, rapport=self._rapport, communes=r.communes,
                         etapes=list(r.etapes.values()), resultats=RESULTATS, profils=profils, variables=variables,
                         fichiers=list(r.textes), journal=self.journal_regles())

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
    def valeurs_dossier(self: "Application", dossier_id: str) -> dict[str, Any]:
        """Codes lus par le pipeline pour un dossier (banc d'essai) : `étape.variable` → valeur."""
        from humatheque.decision.moteur import ephe

        d = self._dossier(dossier_id)
        v: dict[str, Any] = {"type": d.document.type_document,
                             "ephe": ephe(d.provenance_manuelle or (d.provenance.valeur if d.provenance else None))}
        if d.alma:
            v["alma.status"] = d.corrections.get("alma", d.alma.statut)
        if d.sudoc:
            v["sudoc.status"] = d.corrections.get("sudoc", d.sudoc.statut_service)
            v["sudoc.thesis_state"] = d.sudoc.etat
        if d.dewey:
            v["dewey.status"] = d.dewey.statut
        return v

    def essayer_pipeline(self: "Application", valeurs: dict[str, Any]) -> Parcours:
        """Parcours du pipeline pour des valeurs saisies : `type`, `ephe` et `étape.variable`
        (ex. `sudoc.status`). Chaque étape est évaluée jusqu'au premier arrêt."""
        communes = {k: (valeurs.get(k) or None) for k in self.regles.communes}
        etapes, notice = [], None
        for nom, e in self.regles.etapes.items():
            propres = {k: (valeurs.get(f"{nom}.{k}") or None) for k in e.variables}
            r = evaluer(self.regles, nom, {**communes, **propres})
            etapes.append(r)
            if r.resultat in ("conversion", "création"):
                notice = notice or r.notice
            if r.arret:
                break
        dernier = etapes[-1] if etapes else None
        final = "à reprendre" if any("reprendre" in x.resultat for x in etapes) else "terminé"
        return Parcours(etapes=etapes, notice=notice, resultat=dernier.texte if dernier else "", issue=final)

    # -------------------------------------------------------------- arbre du pipeline (édition par nœud)
    def noeud(self: "Application", etape: str, chemin: str) -> Noeud | None:
        """Nœud désigné par son chemin : « 0.2.1 » (rangs), « s » (sinon), « 0.s »… ; « » : nouveau."""
        e = self._etape(etape)
        if not chemin:
            return None
        return _trouver(e, chemin)

    def _etape(self: "Application", etape: str) -> EtapePipeline:
        e = self.regles.etapes.get(etape)
        if e is None:
            raise Introuvable(f"étape {etape}")
        return e

    def modifier_arbre(self: "Application", etape: str, chemin: str, operation: str, auteur: str,
                       noeud: Noeud | None = None) -> EnregistrementRegles:
        """Opérations : `modifier` (remplace conditions et résultat, garde les sous-branches),
        `ajouter` (branche après `chemin`, ou en tête si vide), `sous_branche`, `supprimer`,
        `monter`, `descendre`. L'arbre est réécrit dans pipeline.yaml après vérification."""
        etapes = {k: v.model_copy(deep=True) for k, v in self.regles.etapes.items()}
        e = etapes.get(etape)
        if e is None:
            raise Introuvable(f"étape {etape}")
        nouveau = noeud or Noeud(resultat="poursuite")
        if operation == "modifier":
            cible = _trouver(e, chemin)
            if cible is None:
                raise Introuvable(f"branche {chemin}")
            garder = cible.branches
            for k, v in nouveau.model_dump(exclude={"branches", "sinon"}).items():
                setattr(cible, k, v)
            if garder and not nouveau.resultat:
                cible.branches = garder
            elif nouveau.resultat:
                cible.branches = []
        elif operation == "sous_branche":
            cible = _trouver(e, chemin)
            if cible is None or chemin.endswith("s"):
                raise ActionImpossible("une sous-branche s'ajoute sous une branche")
            if not cible.branches:  # la branche devient un embranchement : son résultat passe à la sous-branche
                nouveau = nouveau if noeud else Noeud(resultat=cible.resultat or "poursuite", notice=cible.notice,
                                                      motif=cible.motif, alerte=cible.alerte)
                cible.resultat = cible.notice = cible.motif = cible.alerte = None
            cible.branches.append(nouveau)
        else:
            liste, i = _parent(e, chemin) if chemin else (e.branches, -1)
            if operation == "ajouter":
                liste.insert(i + 1, nouveau)
            elif operation == "supprimer":
                if i < 0 or chemin.endswith("s"):
                    raise ActionImpossible("le « sinon » d'une étape ne se supprime pas")
                liste.pop(i)
            elif operation in ("monter", "descendre"):
                j = i + (-1 if operation == "monter" else 1)
                if not 0 <= j < len(liste):
                    raise ActionImpossible("la branche est déjà à cette extrémité")
                liste[i], liste[j] = liste[j], liste[i]
            else:
                raise ActionImpossible(f"opération « {operation} » inconnue")
        return self._ecrire_pipeline(etapes, auteur)

    def _ecrire_pipeline(self: "Application", etapes: dict[str, EtapePipeline], auteur: str) -> EnregistrementRegles:
        texte = self.texte_regles(FICHIER_PIPELINE)
        entete = "".join(l + "\n" for l in texte.splitlines() if l.startswith("#") or not l.strip()).split("\n\n")[0]
        donnees = {"communes": self.regles.communes,
                   "etapes": {k: _propre_etape(v) for k, v in etapes.items()}}
        sortie = entete.rstrip() + "\n\n" + yaml.safe_dump(donnees, allow_unicode=True, sort_keys=False, width=110)
        return self.enregistrer_regles(FICHIER_PIPELINE, sortie, auteur)

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

    def banc_essai(self: "Application", dossier_id: str | None = None, valeurs: dict[str, Any] | None = None,
                   profil: str | None = None) -> BancEssai:
        """Parcours du pipeline, données préparées et notice prévisualisée, d'un coup."""
        parcours = self.essayer_pipeline(self.valeurs_dossier(dossier_id) if dossier_id else (valeurs or {}))
        variables, source = self._variables_essai(dossier_id)
        donnees = [{"nom": n, "source": v.source, "valeur": variables.get(n), "disponible": v.disponible}
                   for n, v in self.regles.variables.items()]
        cible = profil or parcours.notice
        apercu = self.apercu_profil(cible, dossier_id) if cible in self.regles.variantes else None
        return BancEssai(source=source, parcours=parcours, donnees=donnees, apercu=apercu, profil_essaye=cible)

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


def _feuilles(noeuds: list[Noeud]) -> list[Noeud]:
    sortie = []
    for n in noeuds:
        sortie += _feuilles(n.branches) if n.branches else [n]
        if n.sinon:
            sortie.append(n.sinon)
    return sortie


def _trouver(e: EtapePipeline, chemin: str) -> Noeud | None:
    liste, n = e.branches, None
    for t in chemin.split("."):
        if t == "s":
            n = e.sinon if n is None else n.sinon
        elif t.isdigit() and int(t) < len(liste):
            n = liste[int(t)]
        else:
            return None
        if n is None:
            return None
        liste = n.branches
    return n


def _parent(e: EtapePipeline, chemin: str) -> tuple[list[Noeud], int]:
    """Liste contenant le nœud désigné, et son rang."""
    *avant, dernier = chemin.split(".")
    if not dernier.isdigit():
        raise ActionImpossible("opération impossible sur un « sinon »")
    liste = e.branches if not avant else (_trouver(e, ".".join(avant)) or Noeud()).branches
    if int(dernier) >= len(liste):
        raise Introuvable(f"branche {chemin}")
    return liste, int(dernier)


def _propre_noeud(n: Noeud) -> dict[str, Any]:
    d: dict[str, Any] = {}
    if n.si:
        d["si"] = n.si
    if n.sauf:
        d["sauf"] = n.sauf
    if n.branches:
        d["branches"] = [_propre_noeud(x) for x in n.branches]
    if n.sinon:
        d["sinon"] = _propre_noeud(n.sinon)
    for k in ("resultat", "notice", "motif", "alerte"):
        if getattr(n, k):
            d[k] = getattr(n, k)
    return d


def _propre_etape(e: EtapePipeline) -> dict[str, Any]:
    d: dict[str, Any] = {"libelle": e.libelle}
    if e.source:
        d["source"] = e.source
    if e.note:
        d["note"] = e.note
    if e.variables:
        d["variables"] = e.variables
    if e.branches:
        d["branches"] = [_propre_noeud(n) for n in e.branches]
    d["sinon"] = _propre_noeud(e.sinon)
    return d


class ReglesRefusees(ActionImpossible):
    """Brouillon refusé : le vérificateur signale au moins une erreur bloquante."""

    def __init__(self, rapport: Rapport) -> None:
        self.rapport = rapport
        super().__init__("modification refusée par le vérificateur : "
                         + "; ".join(f"{c.table} {c.ligne} : {c.message}".replace("  ", " ") for c in rapport.erreurs),
                         details=rapport.erreurs)
