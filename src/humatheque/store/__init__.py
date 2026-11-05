"""Persistance SQLite (SPEC §10) : lots, dossiers (JSON versionné par
`schema_version`) et ingestions (historique des balayages et traitements,
ING-T-003). Une connexion par opération, verrou d'écriture : le store est
partagé par les fils du ThreadPoolExecutor."""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator

from pydantic import BaseModel, Field

from humatheque.notice.versions import VersionNotice
from humatheque.workflow.modeles import Dossier

SCHEMA = """
CREATE TABLE IF NOT EXISTS lots (
    id TEXT PRIMARY KEY,
    cree_le TEXT NOT NULL,
    mode INTEGER NOT NULL,
    declencheur TEXT NOT NULL,
    parametres TEXT NOT NULL,
    nb_documents INTEGER NOT NULL,
    termine_le TEXT
);
CREATE TABLE IF NOT EXISTS dossiers (
    id TEXT PRIMARY KEY,
    lot_id TEXT NOT NULL REFERENCES lots(id),
    rang INTEGER NOT NULL,
    document_id TEXT NOT NULL,
    etat TEXT NOT NULL,            -- en_attente | en_cours | termine
    routage TEXT,
    maj_le TEXT NOT NULL,
    schema_version INTEGER NOT NULL,
    donnees TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS dossiers_lot ON dossiers(lot_id, rang);
CREATE INDEX IF NOT EXISTS dossiers_document ON dossiers(document_id, maj_le);
CREATE TABLE IF NOT EXISTS notice_versions (
    dossier_id TEXT NOT NULL REFERENCES dossiers(id),
    numero INTEGER NOT NULL,
    le TEXT NOT NULL,
    auteur TEXT NOT NULL,
    source TEXT NOT NULL,          -- générée | convertie | édition | report
    statut TEXT NOT NULL,          -- générée | éditée | validée | proposée | reportée | ignorée
    texte TEXT NOT NULL,
    diff TEXT NOT NULL,
    PRIMARY KEY (dossier_id, numero)
);
CREATE TABLE IF NOT EXISTS reglages_admin (
    cle TEXT PRIMARY KEY,
    valeur TEXT NOT NULL,
    maj_le TEXT NOT NULL,
    auteur TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ingestions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    le TEXT NOT NULL,
    type TEXT NOT NULL,            -- balayage | traitement
    declencheur TEXT NOT NULL,     -- manuel | planifie | evenement
    lot_id TEXT,
    documents TEXT NOT NULL,
    resultat TEXT NOT NULL
);
"""


def _maintenant() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class Lot(BaseModel):
    id: str
    cree_le: datetime
    mode: int
    declencheur: str
    parametres: dict[str, Any]
    nb_documents: int
    termine_le: datetime | None = None


class LigneDossier(BaseModel):
    """Résumé d'un dossier, sans le JSON complet."""

    id: str
    lot_id: str
    rang: int
    document_id: str
    etat: str
    routage: str | None
    maj_le: datetime


class Ingestion(BaseModel):
    id: int
    le: datetime
    type: str
    declencheur: str
    lot_id: str | None
    documents: list[str] = Field(default_factory=list)
    resultat: dict[str, Any] = Field(default_factory=dict)


class Store:
    def __init__(self, chemin: Path) -> None:
        self.chemin = chemin
        chemin.parent.mkdir(parents=True, exist_ok=True)
        self._verrou = threading.Lock()
        with self._connexion() as c:
            c.executescript(SCHEMA)

    @contextmanager
    def _connexion(self) -> Iterator[sqlite3.Connection]:
        c = sqlite3.connect(self.chemin, timeout=30)
        c.row_factory = sqlite3.Row
        try:
            yield c
            c.commit()
        finally:
            c.close()

    # ------------------------------------------------------------ lots
    def creer_lot(self, lot: Lot) -> None:
        with self._verrou, self._connexion() as c:
            c.execute("INSERT INTO lots VALUES (?, ?, ?, ?, ?, ?, ?)",
                      (lot.id, lot.cree_le.isoformat(), lot.mode, lot.declencheur,
                       json.dumps(lot.parametres, ensure_ascii=False), lot.nb_documents, None))

    def terminer_lot(self, lot_id: str) -> None:
        with self._verrou, self._connexion() as c:
            c.execute("UPDATE lots SET termine_le = ? WHERE id = ? AND termine_le IS NULL", (_maintenant(), lot_id))

    def rouvrir_lot(self, lot_id: str) -> None:
        with self._verrou, self._connexion() as c:
            c.execute("UPDATE lots SET termine_le = NULL WHERE id = ?", (lot_id,))

    def lot(self, lot_id: str) -> Lot | None:
        with self._connexion() as c:
            r = c.execute("SELECT * FROM lots WHERE id = ?", (lot_id,)).fetchone()
        return self._lot(r) if r else None

    def lots(self, limite: int = 50) -> list[Lot]:
        with self._connexion() as c:
            rows = c.execute("SELECT * FROM lots ORDER BY cree_le DESC, id DESC LIMIT ?", (limite,)).fetchall()
        return [self._lot(r) for r in rows]

    @staticmethod
    def _lot(r: sqlite3.Row) -> Lot:
        return Lot(id=r["id"], cree_le=r["cree_le"], mode=r["mode"], declencheur=r["declencheur"],
                   parametres=json.loads(r["parametres"]), nb_documents=r["nb_documents"],
                   termine_le=r["termine_le"])

    # ------------------------------------------------------------ dossiers
    def enregistrer_dossier(self, d: Dossier, lot_id: str, rang: int, etat: str) -> None:
        donnees = d.model_dump_json()
        with self._verrou, self._connexion() as c:
            c.execute(
                """INSERT INTO dossiers VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET etat = excluded.etat, routage = excluded.routage,
                   maj_le = excluded.maj_le, schema_version = excluded.schema_version, donnees = excluded.donnees""",
                (d.id, lot_id, rang, d.document.id, etat, d.routage, _maintenant(), d.schema_version, donnees))

    def changer_etat(self, dossier_id: str, etat: str) -> None:
        with self._verrou, self._connexion() as c:
            c.execute("UPDATE dossiers SET etat = ?, maj_le = ? WHERE id = ?", (etat, _maintenant(), dossier_id))

    def dossier(self, dossier_id: str) -> Dossier | None:
        with self._connexion() as c:
            r = c.execute("SELECT donnees FROM dossiers WHERE id = ?", (dossier_id,)).fetchone()
        return Dossier.model_validate_json(r["donnees"]) if r else None

    def ligne_dossier(self, dossier_id: str) -> LigneDossier | None:
        with self._connexion() as c:
            r = c.execute("SELECT * FROM dossiers WHERE id = ?", (dossier_id,)).fetchone()
        return self._ligne(r) if r else None

    def dossiers_du_lot(self, lot_id: str) -> list[tuple[LigneDossier, Dossier]]:
        with self._connexion() as c:
            rows = c.execute("SELECT * FROM dossiers WHERE lot_id = ? ORDER BY rang", (lot_id,)).fetchall()
        return [(self._ligne(r), Dossier.model_validate_json(r["donnees"])) for r in rows]

    def lignes_du_lot(self, lot_id: str) -> list[LigneDossier]:
        with self._connexion() as c:
            rows = c.execute("SELECT * FROM dossiers WHERE lot_id = ? ORDER BY rang", (lot_id,)).fetchall()
        return [self._ligne(r) for r in rows]

    def derniers_par_document(self) -> dict[str, LigneDossier]:
        """Dernier dossier de chaque document (statut affiché pour les dossiers MinIO)."""
        with self._connexion() as c:
            rows = c.execute("SELECT * FROM dossiers ORDER BY maj_le").fetchall()
        return {r["document_id"]: self._ligne(r) for r in rows}

    @staticmethod
    def _ligne(r: sqlite3.Row) -> LigneDossier:
        return LigneDossier(id=r["id"], lot_id=r["lot_id"], rang=r["rang"], document_id=r["document_id"],
                            etat=r["etat"], routage=r["routage"], maj_le=r["maj_le"])

    # ------------------------------------------------------------ ingestions
    def noter_ingestion(self, type_: str, declencheur: str, documents: list[str], resultat: dict[str, Any],
                        lot_id: str | None = None) -> int:
        with self._verrou, self._connexion() as c:
            cur = c.execute("INSERT INTO ingestions (le, type, declencheur, lot_id, documents, resultat) "
                            "VALUES (?, ?, ?, ?, ?, ?)",
                            (_maintenant(), type_, declencheur, lot_id, json.dumps(documents, ensure_ascii=False),
                             json.dumps(resultat, ensure_ascii=False)))
            return int(cur.lastrowid or 0)

    def maj_ingestion(self, ingestion_id: int, resultat: dict[str, Any]) -> None:
        with self._verrou, self._connexion() as c:
            c.execute("UPDATE ingestions SET resultat = ? WHERE id = ?",
                      (json.dumps(resultat, ensure_ascii=False), ingestion_id))

    def ingestions(self, limite: int = 100) -> list[Ingestion]:
        with self._connexion() as c:
            rows = c.execute("SELECT * FROM ingestions ORDER BY le DESC, id DESC LIMIT ?", (limite,)).fetchall()
        return [Ingestion(id=r["id"], le=r["le"], type=r["type"], declencheur=r["declencheur"], lot_id=r["lot_id"],
                          documents=json.loads(r["documents"]), resultat=json.loads(r["resultat"])) for r in rows]

    # ------------------------------------------------------------ versions de notice
    def versions(self, dossier_id: str) -> list[VersionNotice]:
        with self._connexion() as c:
            rows = c.execute("SELECT * FROM notice_versions WHERE dossier_id = ? ORDER BY numero",
                             (dossier_id,)).fetchall()
        return [VersionNotice(**dict(r)) for r in rows]

    def ajouter_version(self, v: VersionNotice) -> None:
        with self._verrou, self._connexion() as c:
            c.execute("INSERT INTO notice_versions VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                      (v.dossier_id, v.numero, v.le.isoformat(), v.auteur, v.source, v.statut, v.texte, v.diff))

    def statut_version(self, dossier_id: str, numero: int, statut: str) -> None:
        with self._verrou, self._connexion() as c:
            c.execute("UPDATE notice_versions SET statut = ? WHERE dossier_id = ? AND numero = ?",
                      (statut, dossier_id, numero))

    # ------------------------------------------------------------ réglages d'administration
    def reglage_admin(self, cle: str) -> tuple[Any, str, str] | None:
        """(valeur, maj_le, auteur) ou None."""
        with self._connexion() as c:
            r = c.execute("SELECT * FROM reglages_admin WHERE cle = ?", (cle,)).fetchone()
        return (json.loads(r["valeur"]), r["maj_le"], r["auteur"]) if r else None

    def ecrire_reglage_admin(self, cle: str, valeur: Any, auteur: str) -> None:
        with self._verrou, self._connexion() as c:
            c.execute("INSERT INTO reglages_admin VALUES (?, ?, ?, ?) ON CONFLICT(cle) DO UPDATE SET "
                      "valeur = excluded.valeur, maj_le = excluded.maj_le, auteur = excluded.auteur",
                      (cle, json.dumps(valeur, ensure_ascii=False), _maintenant(), auteur))
