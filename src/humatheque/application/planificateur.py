"""Déclencheurs automatiques (SPEC §3.1, étape 8 ; ING-F-003, ING-F-004).

Un fil d'exécution dans l'application (pas de courtier de messages, §10)
consulte les réglages d'administration à chaque tour (30 s) :

- balayage périodique : toutes les N minutes, les nouveaux dossiers du bucket
  forment un lot (déclencheur `periodique`) ;
- planification : à chaque minute qui correspond à l'expression cron
  (déclencheur `planifie`) ;
- évènements S3 : `POST /minio/evenements` ne fait que signaler ; le lot est
  créé après `delai_secondes` sans nouvel évènement (stabilisation : MinIO
  envoie un évènement par objet, un dossier se dépose en plusieurs fois).

Un seul lot automatique à la fois : tant qu'il tourne, les déclenchements
attendent le tour suivant (les nouveaux dossiers attendent donc le
déclenchement suivant). Suppose une seule instance de l'application.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from humatheque.application import cron

if TYPE_CHECKING:
    from humatheque.application import Application

journal = logging.getLogger("humatheque.planificateur")


def maintenant_local() -> datetime:
    return datetime.now().astimezone()


class EtatPlanificateur(BaseModel):
    actif: bool
    dernier_tour: datetime | None = None
    dernier_balayage: datetime | None = None
    derniere_planification: datetime | None = None
    evenement_en_attente: datetime | None = Field(None, description="dernier évènement S3 non encore traité")
    prochaine_planification: datetime | None = None
    derniers_resultats: list[str] = Field(default_factory=list)


class Planificateur:
    def __init__(self, app: "Application", horloge: Callable[[], datetime] = maintenant_local,
                 periode: float = 30.0) -> None:
        self.app, self.horloge, self.periode = app, horloge, periode
        self._verrou = threading.Lock()
        self._arret = threading.Event()
        self._fil: threading.Thread | None = None
        self.dernier_tour: datetime | None = None
        self.dernier_balayage: datetime | None = None
        self.derniere_planification: datetime | None = None  # minute déjà traitée
        self.evenement_en_attente: datetime | None = None
        self.resultats: list[str] = []

    # -------------------------------------------------------------- cycle de vie
    def demarrer(self) -> None:
        if self._fil and self._fil.is_alive():
            return
        self._arret.clear()
        self._fil = threading.Thread(target=self._boucle, name="planificateur", daemon=True)
        self._fil.start()

    def arreter(self) -> None:
        self._arret.set()
        if self._fil:
            self._fil.join(timeout=5)

    def _boucle(self) -> None:
        while not self._arret.wait(self.periode):
            try:
                self.tick()
            except Exception:  # le fil ne doit jamais mourir
                journal.exception("tour du planificateur en échec")

    # -------------------------------------------------------------- évènements
    def signaler_evenement(self) -> datetime:
        with self._verrou:
            self.evenement_en_attente = self.horloge()
            return self.evenement_en_attente

    # -------------------------------------------------------------- un tour
    def _noter(self, texte: str) -> None:
        self.resultats = [f"{self.horloge():%d/%m %H:%M} {texte}", *self.resultats][:20]
        journal.info(texte)

    def _lancer(self, declencheur: str) -> str:
        from humatheque.application import ActionImpossible

        try:
            lot = self.app.traiter_nouveaux(declencheur)
        except ActionImpossible as e:
            self._noter(f"{declencheur} : échec ({e})")
            return "echec"
        self._noter(f"{declencheur} : " + (f"lot {lot.id} ({lot.nb_documents} document(s))" if lot
                                          else "aucun nouveau dossier"))
        return lot.id if lot else "rien"

    def tick(self) -> str | None:
        """Un tour ; renvoie ce qui a été fait (pour les tests et le journal)."""
        with self._verrou:
            t = self.horloge()
            self.dernier_tour = t
            d = self.app.reglages_admin().declencheurs
            if self.app.lot_automatique_en_cours():
                return "attente : lot automatique en cours"

            # 1. Évènements S3, après stabilisation
            if self.evenement_en_attente is not None:
                if not d.evenements.actif:
                    self.evenement_en_attente = None
                elif t - self.evenement_en_attente >= timedelta(seconds=d.evenements.delai_secondes):
                    self.evenement_en_attente = None
                    return self._lancer("evenement")

            # 2. Planification à heure fixe (une fois par minute qui correspond)
            if d.planification.actif and d.planification.cron:
                minute = t.replace(second=0, microsecond=0)
                if minute != self.derniere_planification and cron.correspond(d.planification.cron, t):
                    self.derniere_planification = minute
                    return self._lancer("planifie")

            # 3. Balayage périodique
            if d.balayage.actif:
                if (self.dernier_balayage is None
                        or t - self.dernier_balayage >= timedelta(minutes=d.balayage.intervalle_minutes)):
                    self.dernier_balayage = t
                    return self._lancer("periodique")
            return None

    def etat(self) -> EtatPlanificateur:
        d = self.app.reglages_admin().declencheurs
        prochaine = None
        if d.planification.actif and d.planification.cron:
            try:
                prochaine = cron.prochaine(d.planification.cron, self.horloge())
            except ValueError:
                prochaine = None
        return EtatPlanificateur(
            actif=bool(self._fil and self._fil.is_alive()), dernier_tour=self.dernier_tour,
            dernier_balayage=self.dernier_balayage, derniere_planification=self.derniere_planification,
            evenement_en_attente=self.evenement_en_attente, prochaine_planification=prochaine,
            derniers_resultats=self.resultats)
