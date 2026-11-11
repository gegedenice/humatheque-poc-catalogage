"""Prompts d'extraction (SPEC §5, VLM-T-005) : lecture et modification depuis
l'administration (page « Prompts »), et correspondance entre les champs du
schéma JSON demandé au VLM et les variables de la notice.

Un enregistrement vérifie que le schéma JSON du prompt reste lisible, archive
la version précédente (`config/prompts/archives/<horodatage>/`) et journalise
(`data/journal_prompts.jsonl`). Le prompt est relu à chaque extraction : la
modification s'applique aux documents traités ensuite (nom et empreinte du
prompt enregistrés dans chaque dossier).
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from humatheque.application import ActionImpossible, Introuvable

if TYPE_CHECKING:
    from humatheque.application import Application

JOURNAL = "journal_prompts.jsonl"


class ChampPrompt(BaseModel):
    champ: str
    consigne: str


class Correspondance(BaseModel):
    """Une ligne « du prompt à la notice »."""

    champ: str | None = Field(None, description="champ du schéma JSON du prompt (None : variable calculée)")
    consigne: str = ""
    variable: str | None = None
    transformation: str = ""
    disponible: bool = True


class VuePrompt(BaseModel):
    nom: str
    type_document: str
    texte: str
    empreinte: str
    champs: list[ChampPrompt]
    erreur: str | None = None


def schema(texte: str) -> dict[str, Any]:
    """Schéma JSON du prompt : du premier « { » au dernier « } »."""
    debut, fin = texte.find("{"), texte.rfind("}")
    if debut < 0 or fin < debut:
        raise ValueError("aucun schéma JSON ({ … }) dans le prompt")
    donnees = json.loads(texte[debut:fin + 1])
    if not isinstance(donnees, dict) or not donnees:
        raise ValueError("le schéma JSON doit être un objet non vide")
    return donnees


class Prompts:
    def _dossier_prompts(self: "Application") -> Path:
        """Même dossier que l'extraction (`config/prompts` de la racine de l'application)."""
        dossier = self.reglages.dossier_config / "prompts"
        if dossier.is_dir():
            return dossier
        from humatheque.config import reglages

        return reglages().dossier_config / "prompts"

    def prompts(self: "Application") -> list[VuePrompt]:
        return [self.prompt(p.name) for p in sorted(self._dossier_prompts().glob("extraction_*.txt"))]

    def prompt(self: "Application", nom: str) -> VuePrompt:
        chemin = self._dossier_prompts() / nom
        if not nom.startswith("extraction_") or not nom.endswith(".txt") or "/" in nom or not chemin.exists():
            raise Introuvable(f"prompt {nom}")
        texte = chemin.read_text(encoding="utf-8")
        try:
            champs = [ChampPrompt(champ=k, consigne=str(v)) for k, v in schema(texte).items()]
            erreur = None
        except ValueError as e:
            champs, erreur = [], str(e)
        return VuePrompt(nom=nom, type_document=nom.removeprefix("extraction_").removesuffix(".txt"), texte=texte,
                         empreinte=hashlib.sha256(texte.encode("utf-8")).hexdigest()[:12], champs=champs,
                         erreur=erreur)

    def enregistrer_prompt(self: "Application", nom: str, texte: str, auteur: str) -> VuePrompt:
        actuel = self.prompt(nom)
        if not auteur.strip():
            raise ActionImpossible("le nom du catalogueur est requis")
        texte = texte.replace("\r\n", "\n")
        try:
            schema(texte)
        except ValueError as e:
            raise ActionImpossible(f"prompt refusé : {e}") from e
        if texte == actuel.texte:
            raise ActionImpossible("aucune modification à enregistrer")
        horodatage = datetime.now(UTC)
        archive = self._dossier_prompts() / "archives" / horodatage.strftime("%Y%m%dT%H%M%S%fZ") / nom
        archive.parent.mkdir(parents=True, exist_ok=True)
        archive.write_text(actuel.texte, encoding="utf-8")
        (self._dossier_prompts() / nom).write_text(texte, encoding="utf-8")
        journal = self.reglages.dossier_data / JOURNAL
        journal.parent.mkdir(parents=True, exist_ok=True)
        with journal.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"le": horodatage.isoformat(), "auteur": auteur.strip(), "fichier": nom,
                                "archive": archive.relative_to(self._dossier_prompts()).as_posix()},
                               ensure_ascii=False) + "\n")
        return self.prompt(nom)

    def journal_prompts(self: "Application", limite: int = 20) -> list[dict[str, Any]]:
        chemin = self.reglages.dossier_data / JOURNAL
        if not chemin.exists():
            return []
        lignes = [l for l in chemin.read_text(encoding="utf-8").splitlines() if l.strip()]
        return [json.loads(l) for l in reversed(lignes[-limite:])]

    def correspondances(self: "Application") -> list[Correspondance]:
        """Du prompt à la notice : chaque champ des schémas → variable(s) de regles/variables.yaml,
        avec la transformation appliquée ; puis les variables calculées (sans champ)."""
        consignes: dict[str, str] = {}
        for p in self.prompts():
            for c in p.champs:
                consignes.setdefault(c.champ, c.consigne)
        prefixe = "extraction."
        par_champ: dict[str, list] = {}
        autres = []
        for nom, v in self.regles.variables.items():
            if v.source.startswith(prefixe):
                par_champ.setdefault(v.source[len(prefixe):], []).append((nom, v))
            else:
                autres.append((nom, v))
        lignes = []
        for champ, consigne in consignes.items():
            for nom, v in par_champ.get(champ) or [(None, None)]:
                lignes.append(Correspondance(champ=champ, consigne=consigne, variable=nom,
                                             transformation=(v.transformation if v else "")
                                             or ("non utilisé dans la notice" if v is None else "aucune"),
                                             disponible=v.disponible if v else True))
        for champ, liste in par_champ.items():  # variables d'extraction dont le champ n'est plus demandé
            if champ not in consignes:
                for nom, v in liste:
                    lignes.append(Correspondance(champ=champ, consigne="(champ absent des prompts)", variable=nom,
                                                 transformation=v.transformation or "—", disponible=False))
        for nom, v in autres:
            lignes.append(Correspondance(variable=nom, transformation=v.transformation or v.description,
                                         consigne=v.source, disponible=v.disponible))
        return lignes
