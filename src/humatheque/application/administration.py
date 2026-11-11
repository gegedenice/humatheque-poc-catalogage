"""Administration (SPEC §3.1, étape 8) : réglages des lots automatiques et des
déclencheurs (enregistrés en base, valeurs initiales lues dans .env et
config/), santé des services, lots automatiques récents."""

from __future__ import annotations

import secrets
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import TYPE_CHECKING, Any

import httpx
from pydantic import BaseModel, Field

from humatheque.application import cron

if TYPE_CHECKING:
    from humatheque.application import Application

CLE_REGLAGES = "lots_automatiques"


class Balayage(BaseModel):
    actif: bool = False
    intervalle_minutes: int = Field(60, ge=5, le=24 * 60)


class Planification(BaseModel):
    actif: bool = False
    cron: str = ""


class Evenements(BaseModel):
    actif: bool = False
    jeton: str = ""
    delai_secondes: int = Field(60, ge=0, le=3600)


class Declencheurs(BaseModel):
    balayage: Balayage = Field(default_factory=Balayage)
    planification: Planification = Field(default_factory=Planification)
    evenements: Evenements = Field(default_factory=Evenements)


class ReglagesAdmin(BaseModel):
    """Réglages appliqués aux lots créés automatiquement (et valeurs proposées par défaut
    dans le formulaire)."""

    fournisseur: str
    modeles: dict[str, str] = Field(default_factory=dict,
                                    description="fournisseur → modèle choisi (absent : modèle de .env)")
    reglages_services: dict[str, dict[str, Any]] = Field(default_factory=dict)
    declencheurs: Declencheurs = Field(default_factory=Declencheurs)
    maj_le: datetime | None = None
    auteur: str | None = None


class FournisseurVue(BaseModel):
    """Un fournisseur VLM : configuration de .env (sans la clé) et modèle retenu."""

    nom: str
    url: str
    modele_env: str
    modele: str = Field(description="modèle utilisé : choix de l'administration, sinon .env")
    cle: bool = Field(description="clé configurée (jamais affichée)")


class Sante(BaseModel):
    composant: str
    url: str | None = None
    ok: bool | None = Field(None, description="None : non configuré ou non utilisé")
    duree: float | None = None
    detail: str = ""


class Administration:
    """Cas d'usage d'administration (mélangés à `Application`)."""

    # -------------------------------------------------------------- réglages
    def reglages_admin(self: "Application") -> ReglagesAdmin:
        enregistre = self.store.reglage_admin(CLE_REGLAGES)
        if enregistre:
            valeur, maj_le, auteur = enregistre
            return ReglagesAdmin(**valeur, maj_le=maj_le, auteur=auteur)
        r = self.reglages
        return ReglagesAdmin(
            fournisseur=r.vlm_fournisseur,
            declencheurs=Declencheurs(
                balayage=Balayage(actif=r.ingestion_auto, intervalle_minutes=r.balayage_intervalle),
                planification=Planification(actif=bool(r.planification_cron.strip()), cron=r.planification_cron.strip()),
                evenements=Evenements(delai_secondes=r.evenements_delai),
            ))

    def modifier_reglages_admin(self: "Application", nouveaux: ReglagesAdmin, auteur: str) -> ReglagesAdmin:
        from humatheque.application import FOURNISSEURS, ActionImpossible

        if not auteur.strip():
            raise ActionImpossible("Indiquez votre nom pour journaliser la modification.")
        if nouveaux.fournisseur not in FOURNISSEURS:
            raise ActionImpossible(f"fournisseur inconnu : {nouveaux.fournisseur}")
        services = self.valider_reglages(nouveaux.reglages_services)
        d = nouveaux.declencheurs
        if d.planification.actif or d.planification.cron:
            try:
                cron.analyser(d.planification.cron)
            except ValueError as e:
                raise ActionImpossible(f"expression cron « {d.planification.cron} » : {e}") from e
        if (d.balayage.actif or d.evenements.actif) and self.depot is None:
            raise ActionImpossible("Balayage et évènements demandent un stockage S3 configuré (MINIO_*).")
        if d.evenements.actif and not d.evenements.jeton:
            d.evenements.jeton = secrets.token_urlsafe(24)
        valeur = nouveaux.model_copy(update={"reglages_services": services}).model_dump(
            mode="json", exclude={"maj_le", "auteur"})
        self.store.ecrire_reglage_admin(CLE_REGLAGES, valeur, auteur.strip())
        return self.reglages_admin()

    # -------------------------------------------------------------- modèles VLM
    def fournisseurs_vlm(self: "Application") -> list[FournisseurVue]:
        from humatheque.extraction.fournisseurs import table_fournisseurs

        choix = self.reglages_admin().modeles
        return [FournisseurVue(nom=n, url=u, modele_env=m, modele=choix.get(n) or m, cle=bool(c))
                for n, (u, m, c) in table_fournisseurs(self.reglages).items()]

    def modeles_accessibles(self: "Application", fournisseur: str) -> list[str]:
        """Modèles proposés par l'API du fournisseur (liste vide si injoignable)."""
        from humatheque.extraction.fournisseurs import lister_modeles

        try:
            return lister_modeles(fournisseur, self.reglages)
        except Exception:  # noqa: BLE001 — affichage : le modèle de .env reste proposé
            return []

    def fournisseur_effectif(self: "Application", fournisseur: str | None) -> tuple[str, str]:
        """(fournisseur, modèle) utilisés pour un lot : modèle choisi en administration, sinon .env."""
        from humatheque.extraction.fournisseurs import table_fournisseurs

        nom = (fournisseur or self.reglages_admin().fournisseur or self.reglages.vlm_fournisseur).lower()
        table = table_fournisseurs(self.reglages)
        modele = self.reglages_admin().modeles.get(nom) or (table[nom][1] if nom in table else "")
        return nom, modele

    def renouveler_jeton(self: "Application", auteur: str) -> ReglagesAdmin:
        r = self.reglages_admin()
        r.declencheurs.evenements.jeton = secrets.token_urlsafe(24)
        return self.modifier_reglages_admin(r, auteur)

    # -------------------------------------------------------------- santé
    def sante(self: "Application") -> list[Sante]:
        """Contrôles indépendants, en parallèle, avec un délai court."""
        controles = [self._sante_service(n, u) for n, u in (
            ("alma-checker", self.reglages.alma_checker_api_url),
            ("sudoc-checker", self.reglages.sudoc_checker_api_url),
            ("idref-linker", self.reglages.idref_qualinka_api_url),
            ("dewey-classifier", self.reglages.dewey_classification_api_url))]
        controles += [self._sante_vlm, self._sante_s3, self._sante_spec]
        with ThreadPoolExecutor(max_workers=len(controles)) as ex:
            return list(ex.map(lambda f: f(), controles))

    @staticmethod
    def _mesurer(composant: str, url: str | None, fonction) -> Sante:
        debut = time.monotonic()
        try:
            ok, detail = fonction()
        except Exception as e:  # tout échec de contrôle est rapporté, jamais levé
            ok, detail = False, str(e)[:300]
        return Sante(composant=composant, url=url, ok=ok, duree=round(time.monotonic() - debut, 2), detail=detail)

    def _sante_service(self, nom: str, url: str):
        def controle() -> Sante:
            if not url:
                return Sante(composant=nom, detail="URL non configurée")

            def appel():
                r = httpx.get(f"{url.rstrip('/')}/health", timeout=10)
                return r.status_code == 200, f"HTTP {r.status_code}"
            return self._mesurer(nom, url, appel)
        return controle

    def _sante_vlm(self: "Application") -> Sante:
        from humatheque.extraction.fournisseurs import fournisseur

        f = self.reglages_admin().fournisseur
        nom = f"VLM ({f})"
        try:
            prov = fournisseur(f, self.reglages)
        except ValueError as e:
            return Sante(composant=nom, ok=False, detail=str(e))
        base = prov.url.rsplit("/chat/completions", 1)[0]

        def appel():
            r = httpx.get(f"{base}/models", headers=prov._entetes, timeout=10)
            return r.status_code == 200, f"HTTP {r.status_code} · modèle {prov.modele}"
        return self._mesurer(nom, prov.url, appel)

    def _sante_s3(self: "Application") -> Sante:
        if self.depot is None:
            return Sante(composant="stockage S3", detail="non configuré (MINIO_*)")

        def appel():
            existe = self.depot.verifier()
            return existe, (f"bucket {self.depot.bucket} accessible" if existe
                            else f"bucket {self.depot.bucket} introuvable")
        return self._mesurer("stockage S3", self.reglages.minio_endpoint or None, appel)

    def _sante_spec(self: "Application") -> Sante:
        e = self.etat_spec()
        return Sante(composant="règles", url=e.emplacement, ok=e.nb_erreurs == 0,
                     detail=f"{e.version} · {e.nb_erreurs} erreur(s), {e.nb_avertissements} avertissement(s)")

    # -------------------------------------------------------------- lots automatiques
    def lots_automatiques_depuis(self: "Application", depuis: datetime | None):
        """Lots créés par un déclencheur automatique après `depuis` (bandeau d'accueil)."""
        return [l for l in self.store.lots(200) if l.declencheur != "manuel"
                and (depuis is None or l.cree_le > depuis)]

    def lot_automatique_en_cours(self: "Application") -> bool:
        return any(l.declencheur != "manuel" and l.termine_le is None for l in self.store.lots(50))
