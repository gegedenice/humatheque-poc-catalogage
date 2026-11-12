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
    type: str = "texte"
    liste: bool = False
    description: str = ""
    type_fixe: bool = Field(False, description="personne / organisme : traitement prévu dans le code")
    present: bool = Field(True, description="champ demandé par au moins un prompt")


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
        from humatheque.config import charger_yaml

        ctx = charger_yaml("contexte")
        fixes = {**{c: "personne" for c in ctx["personnes"]}, **{c: "organisme" for c in ctx["organismes"]}}
        lignes = []

        def ligne(champ: str, consigne: str, nom: str | None, v: Any, present: bool) -> Correspondance:
            return Correspondance(
                champ=champ, consigne=consigne, variable=nom, present=present,
                transformation=(v.transformation if v else "") or ("-" if v is None else "aucune"),
                disponible=v.disponible if v else False, type=fixes.get(champ, v.type if v else "texte"),
                liste=v.liste if v else champ in ctx["multivalues"], description=v.description if v else "",
                type_fixe=champ in fixes)

        for champ, consigne in consignes.items():
            for nom, v in par_champ.get(champ) or [(None, None)]:
                lignes.append(ligne(champ, consigne, nom, v, True))
        for champ, liste in par_champ.items():  # variables d'extraction dont le champ n'est plus demandé
            if champ not in consignes:
                for nom, v in liste:
                    lignes.append(ligne(champ, "(champ absent des prompts)", nom, v, False))
        for nom, v in autres:
            lignes.append(Correspondance(variable=nom, transformation=v.transformation or v.description,
                                         consigne=v.source, disponible=v.disponible))
        return lignes

    def enregistrer_variables(self: "Application", lignes: list[dict[str, Any]], auteur: str):
        """Variables tirées des champs du prompt (« Du prompt à la notice ») : nom, type, liste,
        disponible, description ; réécrit regles/variables.yaml (vérifié, archivé, journalisé).
        Une ligne sans nom de variable retire la variable de ce champ."""
        import re

        import yaml

        from humatheque.regles.chargement import FICHIER_VARIABLES

        prefixe = "extraction."
        actuelles = {n: v for n, v in self.regles.variables.items()}
        nouvelles: dict[str, Any] = {}
        traites: set[str] = set()
        for l in lignes:
            champ, nom = str(l.get("champ") or "").strip(), str(l.get("variable") or "").strip()
            ancien = str(l.get("ancien") or "").strip()
            if ancien:
                traites.add(ancien)
            if not nom:
                continue
            if not re.fullmatch(r"[a-z][a-z0-9_]*", nom):
                raise ActionImpossible(f"nom de variable « {nom} » : minuscules, chiffres et _ seulement")
            if nom in nouvelles:
                raise ActionImpossible(f"variable « {nom} » utilisée pour deux champs")
            avant = actuelles.get(ancien) or actuelles.get(nom)
            nouvelles[nom] = {
                "source": f"{prefixe}{champ}",
                **({"type": l["type"]} if l.get("type") in ("personne", "organisme") else {}),
                **({"liste": True} if l.get("liste") else {}),
                **({} if l.get("disponible") else {"disponible": False}),
                "transformation": (avant.transformation if avant and avant.transformation else "aucune"),
                "description": str(l.get("description") or "").strip() or (avant.description if avant else ""),
            }
            traites.add(nom)
        # ordre : celui du fichier pour les variables existantes, nouvelles à la fin
        sortie: dict[str, Any] = {}
        for n, v in actuelles.items():
            if n in nouvelles:
                sortie[n] = nouvelles.pop(n)
            elif n not in traites:
                sortie[n] = v.model_dump(exclude={"nom"}, exclude_defaults=True)
        sortie.update(nouvelles)
        texte = self.texte_regles(FICHIER_VARIABLES)
        entete = "".join(x + "\n" for x in texte.splitlines() if x.startswith("#"))
        corps = "".join(f"{n}: " + yaml.safe_dump(v, allow_unicode=True, default_flow_style=True, sort_keys=False,
                                                  width=10_000).strip() + "\n" for n, v in sortie.items())
        return self.enregistrer_regles(FICHIER_VARIABLES, entete + "\n" + corps, auteur)
