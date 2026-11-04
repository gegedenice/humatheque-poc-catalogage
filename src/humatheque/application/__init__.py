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
    Accueil, ChampVue, EntreeLot, EtapeVue, EtatSpec, Historique, ImageVue, Indicateurs, LigneException,
    LigneJournal, ResumeLot, VueDossier, VueLot,
)
from humatheque.config import Reglages, reglages as reglages_par_defaut
from humatheque.generateur import vers_marcxml
from humatheque.spec import Spec, charger_spec, verifier
from humatheque.store import Lot, Store
from humatheque.workflow import ETAPES, Document, Dossier, Services, Workflow, services_depuis_reglages
from humatheque.workflow import resolution as res

LIBELLES_ETAPES = {
    "extraction": "Extraction VLM", "provenance": "Provenance", "alma": "Vérification Alma",
    "sudoc": "Vérification Sudoc", "situation": "Situation", "conversion": "Conversion",
    "idref_personnes": "IdRef — personnes", "idref_organisations": "IdRef — organismes",
    "dewey": "Classification Dewey", "generation": "Générateur UNIMARC",
}
FILTRES = {"tous": None, "decisions": "décision", "sorties": "sortie", "conversions": "conversion",
           "incidents": "incident"}
FOURNISSEURS = ["vlm", "openai", "hf", "albert"]


class Introuvable(Exception):
    pass


class ActionImpossible(Exception):
    pass


class ExecuteurSynchrone(Executor):
    """Exécute immédiatement (tests, ligne de commande)."""

    def submit(self, fn: Callable, /, *args: Any, **kwargs: Any) -> Future:
        f: Future = Future()
        try:
            f.set_result(fn(*args, **kwargs))
        except BaseException as e:  # noqa: BLE001 — remonté par le Future
            f.set_exception(e)
        return f


FabriqueServices = Callable[[Spec, str | None], Services]


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


class Application:
    def __init__(self, store: Store | None = None, reglages: Reglages | None = None,
                 fabrique: FabriqueServices | None = None, executeur: Executor | None = None,
                 spec: Spec | None = None) -> None:
        self.reglages = reglages or reglages_par_defaut()
        self.store = store or Store(self.reglages.dossier_data / "humatheque.db")
        self.fabrique = fabrique or (lambda spec, fournisseur: services_depuis_reglages(spec, fournisseur))
        self.executeur = executeur or ThreadPoolExecutor(max_workers=self.reglages.max_workers)
        self._verrou = threading.Lock()
        self.spec = spec or charger_spec(self.reglages)
        self._rapport = verifier(self.spec, statuts_exclus=self.reglages.regles_statuts_exclus)

    # ============================================================ spécification
    def etat_spec(self) -> EtatSpec:
        s, r = self.spec, self._rapport
        grist = None
        if self.reglages.spec_source == "grist":
            grist = not any("Grist injoignable" in a for a in s.avertissements)
        return EtatSpec(source=s.source, emplacement=s.emplacement, version=s.version, grist_joignable=grist,
                        avertissements=s.avertissements, constats=r.constats,
                        nb_erreurs=len(r.erreurs), nb_avertissements=len(r.avertissements))

    def recharger_spec(self) -> EtatSpec:
        spec = charger_spec(self.reglages)
        with self._verrou:
            self.spec = spec
            self._rapport = verifier(spec, statuts_exclus=self.reglages.regles_statuts_exclus)
        return self.etat_spec()

    def accueil(self) -> Accueil:
        return Accueil(spec=self.etat_spec(), fournisseurs=FOURNISSEURS,
                       fournisseur_defaut=self.reglages.vlm_fournisseur,
                       lots=[self._resume(l) for l in self.store.lots(10)])

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
        docs = [d for d in entree.documents if d]
        if not docs:
            raise ActionImpossible("aucun document à traiter")
        if entree.fournisseur and entree.fournisseur not in FOURNISSEURS:
            raise ActionImpossible(f"fournisseur inconnu : {entree.fournisseur}")
        ids = entree.identifiants or [self._identifiant_document(d) for d in docs]
        lot = Lot(id=_identifiant("L"), cree_le=datetime.now(UTC), mode=1 if len(docs) == 1 else 2,
                  declencheur=entree.declencheur, nb_documents=len(docs),
                  parametres={"type_document": entree.type_document,
                              "fournisseur": entree.fournisseur or self.reglages.vlm_fournisseur,
                              "source": entree.source})
        self.store.creer_lot(lot)
        dossiers = []
        for rang, (images, doc_id) in enumerate(zip(docs, ids), start=1):
            d = Dossier(id=_identifiant("D"), document=Document(id=doc_id, type_document=entree.type_document,
                                                                images=images))
            self.store.enregistrer_dossier(d, lot.id, rang, "en_attente")
            dossiers.append(d.id)
        self.store.noter_ingestion("traitement", entree.declencheur, ids, {"documents": len(ids)}, lot.id)
        for dossier_id in dossiers:
            self.executeur.submit(self._traiter, dossier_id)
        return lot

    @staticmethod
    def _identifiant_document(images: list[str]) -> str:
        premiere = images[0]
        if premiere.startswith(("http://", "https://")):
            return premiere.split("?")[0].rstrip("/").rsplit("/", 1)[-1] or premiere
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
            services = self.fabrique(self.spec, (lot.parametres if lot else {}).get("fournisseur"))
            Workflow(services).executer(d, depuis)
        except Exception as e:  # garde-fou : un dossier ne reste jamais « en cours »
            d.routage, d.motif_arret = "incident", f"erreur inattendue : {e}"
            d.noter("dossier", "alerte", d.motif_arret)
        self.store.enregistrer_dossier(d, ligne.lot_id, ligne.rang, "termine")
        self._clore_si_termine(ligne.lot_id)

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
            routage = d.routage or "auto"
            ind.par_routage[routage] = ind.par_routage.get(routage, 0) + 1
            if repris:
                ind.repris += 1
            if routage == "auto" and not repris:
                ind.passes_seuls += 1
            elif routage != "auto" and d.cloture is None:
                ind.en_attente += 1
        return ind

    def _resume(self, lot: Lot) -> ResumeLot:
        dossiers = self.store.dossiers_du_lot(lot.id)
        return ResumeLot(lot=lot, indicateurs=self._indicateurs(dossiers), en_cours=lot.termine_le is None)

    def vue_lot(self, lot_id: str, filtre: str = "tous") -> VueLot:
        lot = self.store.lot(lot_id)
        if lot is None:
            raise Introuvable(f"lot {lot_id}")
        if filtre not in FILTRES:
            filtre = "tous"
        dossiers = self.store.dossiers_du_lot(lot_id)
        file = []
        for ligne, d in dossiers:
            if ligne.etat != "termine" or (d.routage or "auto") == "auto":
                continue
            if FILTRES[filtre] and d.routage != FILTRES[filtre]:
                continue
            ouvertes = [e for e in d.exceptions if not e.resolue]
            etape = d.etape_arret or (ouvertes[0].etape if ouvertes else None)
            motif = d.motif_arret or ("; ".join(e.objet + " — " + e.motif for e in ouvertes[:2]) if ouvertes else None)
            file.append(LigneException(
                dossier_id=d.id, document_id=d.document.id, titre=(d.metadonnees or {}).get("title"),
                etape=etape, etape_libelle=LIBELLES_ETAPES.get(etape or "", etape), motif=motif,
                routage=d.routage or "auto", traite=d.cloture is not None,
                repris=any(e.type == "resolution" for e in d.journal), nb_exceptions=len(ouvertes)))
        file.sort(key=lambda l: l.traite)
        return VueLot(lot=lot, indicateurs=self._indicateurs(dossiers), en_cours=lot.termine_le is None,
                      filtre=filtre, file=file, journal=self._journal_lot(lot, dossiers),
                      etapes=self._avancement(dossiers))

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
                texte = f"{d.document.id} → {d.routage or 'auto'}"
                if d.motif_arret:
                    texte += f" ({d.motif_arret[:90]})"
                fins.append(LigneJournal(horodatage=ligne.maj_le, texte=texte))
        j += sorted(fins, key=lambda l: l.horodatage or lot.cree_le)
        if lot.termine_le:
            auto = sum(1 for _, d in dossiers if (d.routage or "auto") == "auto")
            j.append(LigneJournal(horodatage=lot.termine_le,
                                  texte=f"lot clos : {auto} notice(s) sans exception, {len(dossiers) - auto} à reprendre"))
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
        if d.notice_texte:
            tel.update({"Notice (WinIBW)": "notice.txt", "Notice (MARCXML)": "notice.xml"})
        if d.notice_source_iso:
            tel.update({"Notice source (ISO 2709)": "source.iso", "Notice convertie (ISO 2709)": "convertie.iso",
                        "Notice convertie (WinIBW)": "convertie.txt"})
        titre = (d.contexte.valeurs.get("title") if d.contexte else None) or (d.metadonnees or {}).get("title")
        return VueDossier(dossier=d, lot_id=ligne.lot_id, etat=ligne.etat, titre=titre,
                          etapes=etapes, images=images, champs=champs,
                          exceptions_ouvertes=sum(1 for e in d.exceptions if not e.resolue), telechargements=tel)

    def url_image(self, image: str) -> str:
        if image.startswith(("http://", "https://")):
            return image
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
        if fichier == "notice.txt" and d.notice_texte:
            return f"{base}.txt", d.notice_texte.encode("utf-8"), "text/plain; charset=utf-8"
        if fichier == "notice.xml" and d.notice:
            return f"{base}.xml", vers_marcxml(d.notice).encode("utf-8"), "application/xml"
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
        w.writerow(["document", "dossier", "routage", "situation", "etape_arret", "motif", "ppn_alma",
                    "ppn_source", "exceptions_ouvertes", "traite_hors_outil", "alertes", "version_spec"])
        for ligne, d in self.store.dossiers_du_lot(lot_id):
            w.writerow([d.document.id, d.id, d.routage or ("en cours" if ligne.etat != "termine" else "auto"),
                        d.decision.situation if d.decision else "", d.etape_arret or "", d.motif_arret or "",
                        d.alma.ppn if d.alma and d.alma.ppn else "", d.ppn_source or "",
                        sum(1 for e in d.exceptions if not e.resolue), "oui" if d.cloture else "",
                        " | ".join(d.alertes), d.version_spec or ""])
        return sortie.getvalue()

    def export_notices(self, lot_id: str) -> str:
        """Toutes les notices produites (générées ou converties), séparées par une ligne vide."""
        if self.store.lot(lot_id) is None:
            raise Introuvable(f"lot {lot_id}")
        blocs = []
        for _, d in self.store.dossiers_du_lot(lot_id):
            texte = d.notice_texte or (d.notice_convertie.texte if d.notice_convertie else None)
            if texte:
                blocs.append(texte)
        return "\n\n".join(blocs) + ("\n" if blocs else "")
