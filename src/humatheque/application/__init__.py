"""Couche de services (SPEC §9.1) : chaque écran et chaque action passe par une
fonction de `Application`, qui renvoie des modèles pydantic. Aucune logique
métier dans les routes ni dans les gabarits.

Les dossiers sont traités en tâche de fond par un exécuteur borné
(`MAX_WORKERS`) ; les tests injectent un exécuteur synchrone.
"""

from __future__ import annotations

import csv
import io
import re
import secrets
import threading
from collections.abc import Callable
from concurrent.futures import Executor, Future, ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from humatheque.application.vues import (
    Accueil, ChampVue, DossierBucket, EntreeLot, EtapeVue, EtatSpec, Historique, ImageVue, Indicateurs,
    LigneException, LigneJournal, Reglable, ResumeLot, VueApercu, VueBucket, VueDossier, VueLot, VueLots, VueNotice, resume_version,
)
from humatheque import issues
from humatheque.config import charger_yaml
from humatheque import ingestion
from humatheque.config import Reglages, reglages as reglages_par_defaut
from humatheque.notice import analyse as notice_analyse
from humatheque.notice import validation as notice_validation
from humatheque.notice import versions as nv
from humatheque.regles import Regles, ReglesInvalides, charger_regles, verifier
from humatheque.store import Lot, Store
from humatheque.workflow import ETAPES, Document, Dossier, Services, Workflow, services_depuis_reglages
from humatheque.workflow import resolution as res

LIBELLES_ETAPES = {
    "extraction": "Extraction VLM", "provenance": "Provenance", "alma": "Vérification Alma",
    "sudoc": "Vérification Sudoc", "situation": "Situation", "conversion": "Conversion",
    "idref_personnes": "IdRef — personnes", "idref_organisations": "IdRef — organismes",
    "dewey": "Classification Dewey", "generation": "Générateur UNIMARC",
}
FILTRES = {"tous": None, "a_reprendre": issues.A_REPRENDRE, "incidents": issues.INCIDENT}
FOURNISSEURS = ["vlm", "openai", "hf", "albert"]


class Introuvable(Exception):
    pass


class ActionImpossible(Exception):
    def __init__(self, message: str, details: list | None = None) -> None:
        super().__init__(message)
        self.details = details or []


class Conflit(Exception):
    """Modification concurrente (version de base dépassée)."""


class ExecuteurSynchrone(Executor):
    """Exécute immédiatement (tests, ligne de commande)."""

    def submit(self, fn: Callable, /, *args: Any, **kwargs: Any) -> Future:
        f: Future = Future()
        try:
            f.set_result(fn(*args, **kwargs))
        except BaseException as e:  # noqa: BLE001 — remonté par le Future
            f.set_exception(e)
        return f


FabriqueServices = Callable[[Regles, str | None], Services]


def _identifiant(prefixe: str = "") -> str:
    return f"{prefixe}{datetime.now(UTC):%Y%m%d-%H%M%S}-{secrets.token_hex(3)}"


def documents_depuis_liste(texte: str) -> list[list[str]]:
    """Liste d'URL, une par ligne (format de minio_urls.txt, suffixe « FAIT » ignoré) :
    un document d'une image par ligne."""
    docs = []
    for ligne in (texte or "").splitlines():
        ligne = re.sub(r"\s+FAIT\s*$", "", ligne.strip())
        if ligne and not ligne.startswith("#"):
            docs.append([ligne])
    return docs


from humatheque.application.administration import Administration  # noqa: E402
from humatheque.application.regles_admin import AdministrationRegles, ReglesRefusees  # noqa: E402,F401


class Application(Administration, AdministrationRegles):
    def __init__(self, store: Store | None = None, reglages: Reglages | None = None,
                 fabrique: FabriqueServices | None = None, executeur: Executor | None = None,
                 regles: Regles | None = None, depot: "ingestion.Depot | None | bool" = True) -> None:
        self.reglages = reglages or reglages_par_defaut()
        self.store = store or Store(self.reglages.dossier_data / "humatheque.db")
        self.fabrique = fabrique or (lambda regles, fournisseur: services_depuis_reglages(regles, fournisseur))
        self.executeur = executeur or ThreadPoolExecutor(max_workers=self.reglages.max_workers)
        self._verrou = threading.Lock()
        # depot=True : stockage S3 d'après MINIO_* (None s'il n'est pas configuré)
        self.depot = ingestion.depot_depuis_reglages(self.reglages) if depot is True else (depot or None)
        self.regles = regles or charger_regles(self.reglages)
        self._rapport = verifier(self.regles)

    # ============================================================ règles (SPEC §8)
    def etat_spec(self) -> EtatSpec:
        s, r = self.regles, self._rapport
        return EtatSpec(emplacement=s.emplacement, version=s.version,
                        avertissements=s.avertissements, constats=r.constats,
                        nb_erreurs=len(r.erreurs), nb_avertissements=len(r.avertissements))

    def recharger_spec(self) -> EtatSpec:
        """Relit les règles ; des fichiers illisibles laissent en place les règles chargées."""
        try:
            regles = charger_regles(self.reglages)
        except ReglesInvalides as e:
            raise ActionImpossible(f"règles illisibles, rechargement refusé : {e}") from e
        with self._verrou:
            self.regles = regles
            self._rapport = verifier(regles)
        return self.etat_spec()

    def accueil(self) -> Accueil:
        return Accueil(spec=self.etat_spec(), fournisseurs=FOURNISSEURS,
                       fournisseur_defaut=self.reglages_admin().fournisseur,
                       lots=[self._resume(l) for l in self.store.lots(10)], reglables=self.reglables())

    # ============================================================ paramètres avancés (SPEC §3)
    @staticmethod
    def reglables_config() -> list[Reglable]:
        """Paramètres réglables et défauts de config/services.yaml."""
        cfg = charger_yaml("services")
        return [Reglable(defaut=cfg[r["service"]]["parametres"][r["parametre"]], **r) for r in cfg.get("reglables") or []]

    def reglables(self) -> list[Reglable]:
        """Défauts proposés : config/services.yaml, puis réglages de l'administration."""
        admin = self.reglages_admin().reglages_services
        return [r.model_copy(update={"defaut": admin.get(r.service, {}).get(r.parametre, r.defaut)})
                for r in self.reglables_config()]

    def valider_reglages(self, valeurs: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
        """Réglages du lot : seuls les paramètres déclarés réglables, dans leurs bornes ;
        les valeurs égales au défaut sont omises."""
        permis = {(r.service, r.parametre): r for r in self.reglables_config()}
        retenus: dict[str, dict[str, Any]] = {}
        for service, params in (valeurs or {}).items():
            for parametre, brut in (params or {}).items():
                r = permis.get((service, parametre))
                if r is None:
                    raise ActionImpossible(f"paramètre non réglable : {service}.{parametre}")
                if brut in (None, ""):
                    continue
                if r.choix:
                    if str(brut) not in r.choix:
                        raise ActionImpossible(f"{r.libelle} : « {brut} » hors de {', '.join(r.choix)}")
                    valeur: Any = str(brut)
                else:
                    try:
                        valeur = float(str(brut).replace(",", "."))
                    except ValueError as e:
                        raise ActionImpossible(f"{r.libelle} : nombre attendu, « {brut} » reçu") from e
                    if (r.min is not None and valeur < r.min) or (r.max is not None and valeur > r.max):
                        raise ActionImpossible(f"{r.libelle} : {valeur} hors de [{r.min}, {r.max}]")
                if valeur != r.defaut:
                    retenus.setdefault(service, {})[parametre] = valeur
        return retenus

    # ============================================================ envois
    def enregistrer_televersement(self, fichiers: list[tuple[str, bytes]], un_seul_document: bool) -> list[list[str]]:
        """Images téléversées → data/uploads ; un document multi-images ou un par fichier."""
        if not fichiers:
            raise ActionImpossible("aucun fichier téléversé")
        dossier = self.reglages.dossier_data / "uploads" / secrets.token_hex(6)
        dossier.mkdir(parents=True, exist_ok=True)
        chemins = []
        for i, (nom, contenu) in enumerate(fichiers, start=1):
            sur = re.sub(r"[^\w.-]", "_", Path(nom or "image").name)[:80] or "image"
            chemin = dossier / f"{i:03d}_{sur}"
            chemin.write_bytes(contenu)
            chemins.append(str(chemin))
        return [chemins] if un_seul_document else [[c] for c in chemins]

    def creer_lot(self, entree: EntreeLot) -> Lot:
        # Un dossier vide du bucket reste un document (invalide, ING-F-008) ; ailleurs, on l'écarte.
        docs = entree.documents if entree.source in ("minio", "dossiers") else [d for d in entree.documents if d]
        if not docs:
            raise ActionImpossible("aucun document à traiter")
        if entree.types and len(entree.types) != len(docs):
            raise ActionImpossible("un type par document attendu")
        if entree.fournisseur and entree.fournisseur not in FOURNISSEURS:
            raise ActionImpossible(f"fournisseur inconnu : {entree.fournisseur}")
        ids = entree.identifiants or [self._identifiant_document(d) for d in docs]
        reglages_lot = self.valider_reglages(entree.reglages_services)
        lot = Lot(id=_identifiant("L"), cree_le=datetime.now(UTC), mode=1 if len(docs) == 1 else 2,
                  declencheur=entree.declencheur, nb_documents=len(docs),
                  parametres={"type_document": entree.type_document,
                              "fournisseur": entree.fournisseur or self.reglages.vlm_fournisseur,
                              "source": entree.source, "services": reglages_lot})
        self.store.creer_lot(lot)
        dossiers = []
        types = entree.types or [entree.type_document] * len(docs)
        for rang, (images, doc_id, type_doc) in enumerate(zip(docs, ids, types), start=1):
            d = Dossier(id=_identifiant("D"), document=Document(id=doc_id, type_document=type_doc, images=images))
            self.store.enregistrer_dossier(d, lot.id, rang, "en_attente")
            dossiers.append(d.id)
        self.store.noter_ingestion("traitement", entree.declencheur, ids, {"documents": len(ids)}, lot.id)
        for dossier_id in dossiers:
            self.executeur.submit(self._traiter, dossier_id)
        return lot

    @staticmethod
    def _identifiant_document(images: list[str]) -> str:
        """URL : chemin sans l'hôte (le nom de fichier seul est ambigu : page_de_titre.jpg
        se répète dans chaque dossier du bucket, constaté à l'essai réel du 04/11)."""
        from urllib.parse import unquote, urlsplit

        premiere = images[0]
        if premiere.startswith(("http://", "https://")):
            return unquote(urlsplit(premiere).path).strip("/") or premiere
        return Path(premiere).name

    # ============================================================ traitement
    def _traiter(self, dossier_id: str, depuis: str | None = None) -> None:
        ligne = self.store.ligne_dossier(dossier_id)
        d = self.store.dossier(dossier_id)
        if ligne is None or d is None:
            return
        lot = self.store.lot(ligne.lot_id)
        self.store.changer_etat(dossier_id, "en_cours")
        try:
            services = self.fabrique(self.regles, (lot.parametres if lot else {}).get("fournisseur"))
            # Le dépôt qui a constitué les documents est celui qui lit leurs images.
            if self.depot is not None:
                services.depot = self.depot
            services.reglages_lot = (lot.parametres if lot else {}).get("services") or {}
            Workflow(services).executer(d, depuis)
        except Exception as e:  # garde-fou : un dossier ne reste jamais « en cours »
            d.routage, d.motif_arret = issues.INCIDENT, f"erreur inattendue : {e}"
            d.noter("dossier", "alerte", d.motif_arret)
        self._enregistrer_production(d)
        self.store.enregistrer_dossier(d, ligne.lot_id, ligne.rang, "termine")
        self._clore_si_termine(ligne.lot_id)

    def _enregistrer_production(self, d: Dossier) -> None:
        """Notice produite par le pipeline → version (sans écraser une édition)."""
        if d.notice_texte:
            texte, source = d.notice_texte, "générée"
        elif d.notice_convertie:
            texte, source = d.notice_convertie.texte, "convertie"
        else:
            return
        v = nv.enregistrer_production(self.store.versions(d.id), d.id, texte, source)
        if v is not None:
            self.store.ajouter_version(v)
            if v.statut == "proposée":
                d.noter("generation", "alerte", f"régénération proposée (version {v.numero}) : "
                                                "une version éditée existe, elle n'est pas écrasée")

    def _clore_si_termine(self, lot_id: str) -> None:
        lignes = self.store.lignes_du_lot(lot_id)
        if lignes and all(l.etat == "termine" for l in lignes):
            self.store.terminer_lot(lot_id)

    def _relancer(self, d: Dossier, depuis: str | None = None) -> None:
        ligne = self.store.ligne_dossier(d.id)
        assert ligne is not None
        self.store.enregistrer_dossier(d, ligne.lot_id, ligne.rang, "en_attente")
        self.store.rouvrir_lot(ligne.lot_id)
        self.executeur.submit(self._traiter, d.id, depuis)

    # ============================================================ lots
    def _indicateurs(self, dossiers: list[tuple[Any, Dossier]]) -> Indicateurs:
        ind = Indicateurs(documents=len(dossiers))
        for ligne, d in dossiers:
            repris = any(e.type == "resolution" for e in d.journal)
            if ligne.etat != "termine":
                ind.en_cours += 1
                continue
            routage = d.routage or issues.TERMINE
            ind.par_routage[routage] = ind.par_routage.get(routage, 0) + 1
            if repris:
                ind.repris += 1
            if routage == issues.TERMINE and not repris:
                ind.passes_seuls += 1
            elif routage != issues.TERMINE and d.cloture is None:
                ind.en_attente += 1
        return ind

    def _resume(self, lot: Lot) -> ResumeLot:
        dossiers = self.store.dossiers_du_lot(lot.id)
        return ResumeLot(lot=lot, indicateurs=self._indicateurs(dossiers), en_cours=lot.termine_le is None)

    def vue_lots(self, etat: str = "", declencheur: str = "", type_document: str = "",
                 limite: int = 200) -> VueLots:
        """Lots les plus récents, filtrés par état (en_cours / termines), déclencheur et type."""
        if etat not in ("", "en_cours", "termines"):
            raise ActionImpossible(f"état de lot inconnu : {etat}")
        tous = self.store.lots(limite)
        retenus = [l for l in tous
                   if (not etat or (l.termine_le is None) == (etat == "en_cours"))
                   and (not declencheur or l.declencheur == declencheur)
                   and (not type_document or l.parametres.get("type_document") == type_document)]
        resumes = [self._resume(l) for l in retenus]
        return VueLots(lots=resumes, a_reprendre=sorted((r for r in resumes if not r.en_cours and r.indicateurs.en_attente),
                                           key=lambda r: -r.indicateurs.en_attente),
                       etat=etat, declencheur=declencheur, type_document=type_document,
                       declencheurs=sorted({l.declencheur for l in tous}), total=len(tous))

    def vue_lot(self, lot_id: str, filtre: str = "tous") -> VueLot:
        lot = self.store.lot(lot_id)
        if lot is None:
            raise Introuvable(f"lot {lot_id}")
        if filtre not in FILTRES:
            filtre = "tous"
        dossiers = self.store.dossiers_du_lot(lot_id)
        file, passes = [], []
        for ligne, d in dossiers:
            if ligne.etat == "termine" and (d.routage or issues.TERMINE) == issues.TERMINE:
                passes.append(LigneException(
                    dossier_id=d.id, document_id=d.document.id, titre=(d.metadonnees or {}).get("title"),
                    etape=d.etape_arret, etape_libelle=LIBELLES_ETAPES.get(d.etape_arret or "", d.etape_arret),
                    motif=d.motif_arret or (d.decision.motif if d.decision else None), routage=issues.TERMINE,
                    repris=any(e.type == "resolution" for e in d.journal)))
                continue
            if ligne.etat != "termine":
                continue
            if FILTRES[filtre] and d.routage != FILTRES[filtre]:
                continue
            ouvertes = [e for e in d.exceptions if not e.resolue]
            etape = d.etape_arret or (ouvertes[0].etape if ouvertes else None)
            motif = d.motif_arret or ("; ".join(e.objet + " — " + e.motif for e in ouvertes[:2]) if ouvertes else None)
            file.append(LigneException(
                dossier_id=d.id, document_id=d.document.id, titre=(d.metadonnees or {}).get("title"),
                etape=etape, etape_libelle=LIBELLES_ETAPES.get(etape or "", etape), motif=motif,
                routage=d.routage or issues.TERMINE, traite=d.cloture is not None,
                repris=any(e.type == "resolution" for e in d.journal), nb_exceptions=len(ouvertes)))
        file.sort(key=lambda l: l.traite)
        return VueLot(lot=lot, indicateurs=self._indicateurs(dossiers), en_cours=lot.termine_le is None,
                      filtre=filtre, file=file, journal=self._journal_lot(lot, dossiers),
                      etapes=self._avancement(dossiers), passes=passes)

    def _avancement(self, dossiers: list[tuple[Any, Dossier]]) -> list[dict[str, Any]]:
        lignes = []
        for nom in ETAPES:
            faits = sum(1 for _, d in dossiers if d.etapes[nom].statut not in ("a_faire",))
            en_echec = sum(1 for _, d in dossiers if d.etapes[nom].statut not in ("a_faire", "ok", "ignoree"))
            lignes.append({"etape": nom, "libelle": LIBELLES_ETAPES[nom], "faits": faits, "en_echec": en_echec,
                           "total": len(dossiers)})
        return lignes

    @staticmethod
    def _journal_lot(lot: Lot, dossiers: list[tuple[Any, Dossier]]) -> list[LigneJournal]:
        j = [LigneJournal(horodatage=lot.cree_le, texte=f"lot ouvert : {lot.nb_documents} document(s), "
                                                        f"{lot.parametres.get('type_document')}, "
                                                        f"fournisseur {lot.parametres.get('fournisseur')}")]
        fins = []
        for ligne, d in dossiers:
            if ligne.etat == "termine":
                texte = f"{d.document.id} → {d.routage or issues.TERMINE}"
                if d.motif_arret:
                    texte += f" ({d.motif_arret[:90]})"
                fins.append(LigneJournal(horodatage=ligne.maj_le, texte=texte))
        j += sorted(fins, key=lambda l: l.horodatage or lot.cree_le)
        if lot.termine_le:
            auto = sum(1 for _, d in dossiers if (d.routage or issues.TERMINE) == issues.TERMINE)
            j.append(LigneJournal(horodatage=lot.termine_le,
                                  texte=f"lot clos : {auto} terminé(s), {len(dossiers) - auto} à reprendre ou en incident"))
        return j

    def historique(self) -> Historique:
        return Historique(ingestions=self.store.ingestions(), lots=[self._resume(l) for l in self.store.lots(100)])

    # ============================================================ dossiers
    def _dossier(self, dossier_id: str) -> Dossier:
        d = self.store.dossier(dossier_id)
        if d is None:
            raise Introuvable(f"dossier {dossier_id}")
        return d

    def vue_dossier(self, dossier_id: str) -> VueDossier:
        d = self._dossier(dossier_id)
        ligne = self.store.ligne_dossier(dossier_id)
        assert ligne is not None
        etapes = [EtapeVue(nom=n, libelle=LIBELLES_ETAPES[n], statut=e.statut, routage=e.routage, motif=e.motif,
                           duree=e.duree, tentatives=e.tentatives) for n, e in d.etapes.items()]
        images = [ImageVue(url=self.url_image(i), nom=Path(i.split("?")[0]).name) for i in d.document.images]
        brut = d.extraction.extraction if d.extraction else {}
        ctx = d.contexte.valeurs if d.contexte else {}
        champs = [ChampVue(champ=c, brut=brut.get(c), normalise=ctx.get(c, (d.metadonnees or {}).get(c)))
                  for c in (d.metadonnees or {})]
        tel = {}
        if d.notice_texte or self.store.versions(d.id):
            tel.update({"Notice (WinIBW)": "notice.txt", "Notice (MARCXML)": "notice.xml"})
        if d.notice_source_iso:
            tel.update({"Notice source (ISO 2709)": "source.iso", "Notice convertie (ISO 2709)": "convertie.iso",
                        "Notice convertie (WinIBW)": "convertie.txt"})
        titre = (d.contexte.valeurs.get("title") if d.contexte else None) or (d.metadonnees or {}).get("title")
        return VueDossier(dossier=d, lot_id=ligne.lot_id, etat=ligne.etat, titre=titre,
                          etapes=etapes, images=images, champs=champs,
                          exceptions_ouvertes=sum(1 for e in d.exceptions if not e.resolue), telechargements=tel)

    def vue_apercu(self, dossier_id: str) -> VueApercu:
        """Images et notice finale, sans édition : version courante (validée, éditée ou
        générée), à défaut la notice générée ou convertie."""
        v = self.vue_dossier(dossier_id)
        d = v.dossier
        versions = self.store.versions(d.id)
        notice, origine = None, None
        if versions:
            courante = versions[-1]
            notice, origine = courante.texte, f"version {courante.numero} · {courante.statut}"
        elif d.notice_texte:
            notice, origine = d.notice_texte, "notice générée"
        elif d.notice_convertie:
            notice, origine = d.notice_convertie.texte, f"notice convertie depuis le PPN {d.ppn_source}"
        profil = (d.notice.profil if d.notice else None) or (d.notice_convertie.profil_cible if d.notice_convertie else None)
        return VueApercu(dossier_id=d.id, lot_id=v.lot_id, document_id=d.document.id,
                         type_document=d.document.type_document, titre=v.titre, routage=d.routage or issues.TERMINE,
                         situation=d.decision.situation if d.decision else None, motif_arret=d.motif_arret,
                         images=v.images, notice=notice, origine=origine, profil=profil, alertes=d.alertes,
                         telechargements=v.telechargements)

    def url_image(self, image: str) -> str:
        if image.startswith(("http://", "https://")):
            return image
        if image.startswith("s3://"):
            from urllib.parse import quote

            return "/minio/objet?uri=" + quote(image, safe="")
        uploads = (self.reglages.dossier_data / "uploads").resolve()
        try:
            return "/fichiers/" + Path(image).resolve().relative_to(uploads).as_posix()
        except ValueError:
            return ""

    def chemin_fichier(self, relatif: str) -> Path:
        """Fichier téléversé servi par /fichiers (jamais hors de data/uploads)."""
        uploads = (self.reglages.dossier_data / "uploads").resolve()
        chemin = (uploads / relatif).resolve()
        if uploads not in chemin.parents or not chemin.is_file():
            raise Introuvable(relatif)
        return chemin

    def telecharger(self, dossier_id: str, fichier: str) -> tuple[str, bytes, str]:
        """(nom, contenu, type MIME) d'un fichier du dossier."""
        d = self._dossier(dossier_id)
        base = re.sub(r"\W", "_", d.document.id)
        texte = self._texte_a_exporter(d)
        if fichier == "notice.txt" and texte:
            return f"{base}.txt", texte.encode("utf-8"), "text/plain; charset=utf-8"
        if fichier == "notice.xml" and texte:
            return f"{base}.xml", notice_analyse.vers_marcxml(texte).encode("utf-8"), "application/xml"
        if fichier == "source.iso" and d.notice_source_iso:
            return f"{base}-source-{d.ppn_source}.iso", d.notice_source_iso.encode("utf-8"), "application/marc"
        if fichier == "convertie.iso" and d.notice_convertie:
            return f"{base}-convertie.iso", d.notice_convertie.iso.encode("utf-8"), "application/marc"
        if fichier == "convertie.txt" and d.notice_convertie:
            return f"{base}-convertie.txt", d.notice_convertie.texte.encode("utf-8"), "text/plain; charset=utf-8"
        raise Introuvable(f"{fichier} pour le dossier {dossier_id}")

    # ============================================================ actions
    def relancer(self, dossier_id: str, depuis: str | None = None) -> VueDossier:
        d = self._dossier(dossier_id)
        if depuis is not None and depuis not in ETAPES:
            raise ActionImpossible(f"étape inconnue : {depuis}")
        d.noter(depuis or "dossier", "relance", "relance manuelle")
        self._relancer(d, depuis)
        return self.vue_dossier(dossier_id)

    def _resolution(self, dossier_id: str, action: Callable[[Dossier], Any], relancer: bool) -> VueDossier:
        d = self._dossier(dossier_id)
        try:
            action(d)
        except res.ResolutionImpossible as e:
            raise ActionImpossible(str(e)) from e
        if relancer:
            self._relancer(d)
        else:
            ligne = self.store.ligne_dossier(dossier_id)
            assert ligne is not None
            self.store.enregistrer_dossier(d, ligne.lot_id, ligne.rang, ligne.etat)
        return self.vue_dossier(dossier_id)

    def choisir_provenance(self, dossier_id: str, valeur: str, auteur: str, commentaire: str = "") -> VueDossier:
        return self._resolution(dossier_id, lambda d: res.choisir_provenance(d, valeur, auteur, commentaire), True)

    def declarer_faux_positif(self, dossier_id: str, service: str, commentaire: str, auteur: str) -> VueDossier:
        return self._resolution(dossier_id, lambda d: res.declarer_faux_positif(d, service, commentaire, auteur), True)

    def choisir_ppn(self, dossier_id: str, index: int, ppn: str | None, auteur: str) -> VueDossier:
        return self._resolution(dossier_id, lambda d: res.choisir_ppn(d, index, ppn, auteur), True)

    def choisir_ppns(self, dossier_id: str, choix: dict[int, str | None], auteur: str) -> VueDossier:
        """Plusieurs exceptions IdRef résolues d'un coup (index → PPN, None = sans lien) :
        tout ou rien (un PPN invalide n'applique aucun choix), une seule relance."""
        if not choix:
            raise ActionImpossible("aucun choix de PPN")

        def appliquer(d: Dossier) -> None:
            for index, ppn in sorted(choix.items()):
                res.choisir_ppn(d, index, ppn, auteur)
        return self._resolution(dossier_id, appliquer, True)

    def choisir_dewey(self, dossier_id: str, index: int, code: str, auteur: str) -> VueDossier:
        return self._resolution(dossier_id, lambda d: res.choisir_dewey(d, index, code, auteur), True)

    def traiter_hors_outil(self, dossier_id: str, commentaire: str, auteur: str) -> VueDossier:
        return self._resolution(dossier_id, lambda d: res.traiter_hors_outil(d, commentaire, auteur), False)

    # ============================================================ exports
    def export_csv(self, lot_id: str) -> str:
        if self.store.lot(lot_id) is None:
            raise Introuvable(f"lot {lot_id}")
        sortie = io.StringIO()
        w = csv.writer(sortie)
        w.writerow(["document", "dossier", "issue", "regle_de_decision", "etape_arret", "motif", "ppn_alma",
                    "ppn_source", "exceptions_ouvertes", "traite_hors_outil", "alertes", "version_spec"])
        for ligne, d in self.store.dossiers_du_lot(lot_id):
            w.writerow([d.document.id, d.id, d.routage or ("en cours" if ligne.etat != "termine" else issues.TERMINE),
                        d.decision.situation if d.decision else "", d.etape_arret or "", d.motif_arret or "",
                        d.alma.ppn if d.alma and d.alma.ppn else "", d.ppn_source or "",
                        sum(1 for e in d.exceptions if not e.resolue), "oui" if d.cloture else "",
                        " | ".join(d.alertes), d.version_spec or ""])
        return sortie.getvalue()

    def _texte_a_exporter(self, d: Dossier) -> str | None:
        """Dernière version validée, à défaut la version générée (SPEC §8)."""
        v = nv.a_exporter(self.store.versions(d.id))
        if v is not None:
            return v.texte
        return d.notice_texte or (d.notice_convertie.texte if d.notice_convertie else None)

    def export_notices(self, lot_id: str) -> str:
        """Toutes les notices produites (validées, sinon générées ou converties), séparées par une ligne vide."""
        if self.store.lot(lot_id) is None:
            raise Introuvable(f"lot {lot_id}")
        blocs = []
        for _, d in self.store.dossiers_du_lot(lot_id):
            texte = self._texte_a_exporter(d)
            if texte:
                blocs.append(texte)
        return "\n\n".join(blocs) + ("\n" if blocs else "")

    # ============================================================ notice éditable (SPEC §9.2)
    def _profil(self, d: Dossier) -> str | None:
        if d.notice:
            return d.notice.profil
        if d.notice_convertie:
            return d.notice_convertie.profil_cible
        return d.decision.profil_cible if d.decision else None

    def vue_notice(self, dossier_id: str) -> VueNotice:
        d = self._dossier(dossier_id)
        versions = self.store.versions(dossier_id)
        c = nv.courante(versions)
        p = nv.proposition(versions)
        profil = self._profil(d)
        return VueNotice(dossier_id=dossier_id, profil=profil, version=c.numero if c else None,
                         statut=c.statut if c else None, source=c.source if c else None,
                         texte=c.texte if c else "",
                         marqueurs=notice_validation.valider(c.texte, self.regles, profil) if c else [],
                         proposition=resume_version(p) if p else None,
                         versions=[resume_version(v) for v in reversed(versions)])

    def valider_texte(self, dossier_id: str, texte: str) -> list[notice_validation.Marqueur]:
        d = self._dossier(dossier_id)
        return notice_validation.valider(texte, self.regles, self._profil(d))

    def _version_ajoutee(self, d: Dossier, v: nv.VersionNotice, quoi: str) -> VueNotice:
        self.store.ajouter_version(v)
        d.noter("notice", "resolution", {"action": quoi, "version": v.numero, "statut": v.statut}, v.auteur)
        ligne = self.store.ligne_dossier(d.id)
        assert ligne is not None
        self.store.enregistrer_dossier(d, ligne.lot_id, ligne.rang, ligne.etat)
        return self.vue_notice(d.id)

    def enregistrer_notice(self, dossier_id: str, texte: str, auteur: str, base: int) -> VueNotice:
        if not auteur.strip():
            raise ActionImpossible("un auteur est requis")
        d = self._dossier(dossier_id)
        try:
            v = nv.editer(self.store.versions(dossier_id), dossier_id, texte, auteur.strip(), base)
        except nv.ConflitVersion as e:
            raise Conflit(str(e)) from e
        return self._version_ajoutee(d, v, "édition")

    def valider_notice(self, dossier_id: str, auteur: str) -> VueNotice:
        if not auteur.strip():
            raise ActionImpossible("un auteur est requis")
        d = self._dossier(dossier_id)
        versions = self.store.versions(dossier_id)
        c = nv.courante(versions)
        erreurs = [m for m in notice_validation.valider(c.texte, self.regles, self._profil(d))
                   if m.gravite == "erreur"] if c else []
        try:
            v = nv.valider(versions, dossier_id, auteur.strip(), erreurs)
        except nv.ValidationRefusee as e:
            raise ActionImpossible(f"validation refusée : {len(e.erreurs)} erreur(s) dans la notice", e.erreurs) from e
        except nv.ConflitVersion as e:
            raise ActionImpossible(str(e)) from e
        return self._version_ajoutee(d, v, "validation")

    def reporter_proposition(self, dossier_id: str, numero: int, auteur: str) -> VueNotice:
        d = self._dossier(dossier_id)
        try:
            v = nv.reporter(self.store.versions(dossier_id), dossier_id, numero, auteur.strip() or "catalogueur")
        except nv.ConflitVersion as e:
            raise ActionImpossible(str(e)) from e
        self.store.statut_version(dossier_id, numero, "reportée")
        return self._version_ajoutee(d, v, f"report de la régénération {numero}")

    def ignorer_proposition(self, dossier_id: str, numero: int, auteur: str) -> VueNotice:
        d = self._dossier(dossier_id)
        p = nv.proposition(self.store.versions(dossier_id))
        if p is None or p.numero != numero:
            raise ActionImpossible("aucune proposition de régénération en attente")
        self.store.statut_version(dossier_id, numero, "ignorée")
        d.noter("notice", "resolution", {"action": "régénération ignorée", "version": numero},
                auteur.strip() or "catalogueur")
        ligne = self.store.ligne_dossier(d.id)
        assert ligne is not None
        self.store.enregistrer_dossier(d, ligne.lot_id, ligne.rang, ligne.etat)
        return self.vue_notice(dossier_id)

    # ============================================================ ingestion S3 et dossiers (SPEC §3.1)
    def _depot(self) -> "ingestion.Depot":
        if self.depot is None:
            raise ActionImpossible("Stockage S3 non configuré : renseignez MINIO_ENDPOINT, MINIO_BUCKET, "
                                   "MINIO_ACCESS_KEY et MINIO_SECRET_KEY dans .env.")
        return self.depot

    def _documents_du_depot(self) -> list[ingestion.DocumentDetecte]:
        try:
            return ingestion.documents_du_depot(self._depot())
        except ingestion.DepotIndisponible as e:
            raise ActionImpossible(f"Stockage S3 injoignable : {e}") from e

    def balayer(self, declencheur: str = "manuel") -> VueBucket:
        """Liste les dossiers du bucket avec leur dernier statut ; le balayage est journalisé (ING-T-003)."""
        if self.depot is None:
            return VueBucket(configure=False)
        try:
            documents = self._documents_du_depot()
        except ActionImpossible as e:
            self.store.noter_ingestion("balayage", declencheur, [], {"erreur": str(e)})
            return VueBucket(configure=True, bucket=self.depot.bucket, prefixe=self.depot.prefixe, erreur=str(e))
        derniers = self.store.derniers_par_document()
        lignes = []
        for doc in documents:
            dernier = derniers.get(doc.id)
            # Dossier modifié depuis son dernier traitement : signalé, jamais retraité d'office
            modifie = False
            if dernier is not None:
                precedent = self.store.dossier(dernier.id)
                modifie = precedent is not None and precedent.document.images != doc.images
            lignes.append(DossierBucket(document=doc, nouveau=dernier is None, modifie=modifie,
                                        dossier_id=dernier.id if dernier else None,
                                        lot_id=dernier.lot_id if dernier else None,
                                        etat=dernier.etat if dernier else None,
                                        routage=dernier.routage if dernier else None))
        nouveaux = [l.document.id for l in lignes if l.nouveau]
        self.store.noter_ingestion("balayage", declencheur, [l.document.id for l in lignes],
                                   {"documents": len(lignes), "nouveaux": len(nouveaux)})
        return VueBucket(configure=True, bucket=self.depot.bucket, prefixe=self.depot.prefixe, dossiers=lignes)

    def lancer_selection(self, identifiants: list[str], fournisseur: str | None = None,
                         declencheur: str = "manuel", reglages: dict[str, dict[str, Any]] | None = None) -> Lot:
        """Traite une sélection de dossiers du bucket (ING-F-005) ; le type vient du dossier TH / MM."""
        if not identifiants:
            raise ActionImpossible("aucun dossier sélectionné")
        par_id = {d.id: d for d in self._documents_du_depot()}
        inconnus = [i for i in identifiants if i not in par_id]
        if inconnus:
            raise ActionImpossible(f"dossier(s) absent(s) du bucket : {', '.join(inconnus)}")
        return self._lot_de_documents([par_id[i] for i in identifiants], fournisseur, "minio", declencheur, reglages)

    def traiter_nouveaux(self, declencheur: str) -> Lot | None:
        """Nouveaux dossiers (absents de l'historique) → un lot. Prévu pour le balayage
        périodique, la planification et les évènements S3 (ING-F-003, ING-F-004), qui ne
        sont pas activés tant que l'arbitrage n'est pas rendu (INGESTION_AUTO=false)."""
        vue = self.balayer(declencheur)
        if vue.erreur:
            raise ActionImpossible(vue.erreur)
        # Un dossier de type inconnu n'est pas traité automatiquement (il reste signalé)
        nouveaux = [l.document for l in vue.dossiers if l.nouveau and l.document.type_document]
        if not nouveaux:
            return None
        admin = self.reglages_admin()
        return self._lot_de_documents(nouveaux, admin.fournisseur, "minio", declencheur, admin.reglages_services)

    def televerser_dossiers(self, fichiers: list[tuple[str, bytes]], type_par_defaut: str,
                            fournisseur: str | None = None, reglages: dict[str, dict[str, Any]] | None = None) -> Lot:
        """Téléversement d'une arborescence de dossiers : chaque dossier est un document,
        le type vient d'un dossier parent TH / MM, à défaut du formulaire."""
        if not fichiers:
            raise ActionImpossible("aucun fichier téléversé")
        racine = self.reglages.dossier_data / "uploads" / secrets.token_hex(6)
        chemins = []
        for nom, contenu in fichiers:
            parties = [re.sub(r"[^\w.() -]", "_", p)[:120] for p in (nom or "").replace("\\", "/").split("/")
                       if p not in ("", ".", "..")]
            if len(parties) < 2:
                raise ActionImpossible("Téléversement de dossiers : chaque fichier doit être dans un dossier.")
            relatif = "/".join(parties)
            (racine / relatif).parent.mkdir(parents=True, exist_ok=True)
            (racine / relatif).write_bytes(contenu)
            chemins.append(relatif)
        documents = ingestion.constituer_documents(chemins, type_par_defaut, vers_image=lambda c: str(racine / c))
        if not documents:
            raise ActionImpossible("Aucun dossier contenant des images.")
        return self._lot_de_documents(documents, fournisseur, "dossiers", "manuel", reglages)

    def _lot_de_documents(self, documents: list[ingestion.DocumentDetecte], fournisseur: str | None, source: str,
                          declencheur: str, reglages: dict[str, dict[str, Any]] | None = None) -> Lot:
        sans_type = [d.id for d in documents if d.type_document is None]
        if sans_type:
            raise ActionImpossible(f"type de document inconnu pour : {', '.join(sans_type)} "
                                   "(dossier parent TH ou MM attendu)")
        types = [d.type_document for d in documents]
        return self.creer_lot(EntreeLot(type_document=types[0], types=types, fournisseur=fournisseur,
                                        documents=[d.images for d in documents],
                                        identifiants=[d.id for d in documents], source=source,
                                        declencheur=declencheur, reglages_services=reglages or {}))

    def objet_s3(self, u: str) -> tuple[bytes, str]:
        """Image du bucket relayée à l'interface (contenu, type MIME)."""
        import mimetypes

        depot = self._depot()
        try:
            contenu = depot.lire(ingestion.cle_depuis_uri(u, depot.bucket))
        except ingestion.DepotIndisponible as e:
            raise Introuvable(str(e)) from e
        return contenu, mimetypes.guess_type(u)[0] or "application/octet-stream"
