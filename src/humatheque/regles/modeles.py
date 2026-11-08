"""Modèles des règles de catalogage (SPEC §8) : variables, profils, décisions.

Les fichiers YAML sont validés à la lecture ; les conditions rule-engine sont
compilées par le moteur (`regles/moteur.py`).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Variable(BaseModel):
    model_config = ConfigDict(extra="allow")

    nom: str
    source: str = ""
    type: Literal["texte", "personne", "organisme"] = "texte"
    liste: bool = False
    disponible: bool = True
    description: str = ""


class Ligne(BaseModel):
    """Une ligne de profil : une zone écrite comme dans WinIBW (`ligne || repli`)."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    ligne: str
    si: str | None = None
    priorite: int | None = None
    remarque: str | None = None
    a_valider: bool = Field(False, alias="à_valider", description="ligne encore en discussion")
    actif: bool = Field(True, description="false : zone documentée mais non produite")
    joindre: dict[str, list[str]] = Field(default_factory=dict,
                                          description="variable liste → [séparateur, dernier séparateur]")
    numero: int = Field(0, description="rang de la ligne dans le profil (1, 2…)")

    @field_validator("ligne", "remarque", "si", mode="before")
    @classmethod
    def _texte(cls, v: Any) -> Any:
        return None if v is None else str(v)

    @field_validator("joindre", mode="before")
    @classmethod
    def _joindre(cls, v: Any) -> Any:
        if v is None:
            return {}
        if isinstance(v, dict):
            return {k: ([s] if isinstance(s, str) else list(s)) for k, s in v.items()}
        return v

    @property
    def principal(self) -> str:
        return self.ligne.partition("||")[0].strip()

    @property
    def repli(self) -> str:
        return self.ligne.partition("||")[2].strip()

    @property
    def etiquette(self) -> str:
        return self.ligne.strip()[:3]

    @property
    def statut(self) -> str:
        """« à valider » ou « validée » (annotation des zones produites)."""
        return "à valider" if self.a_valider else "validée"

    @property
    def trace(self) -> str:
        return f"{self.etiquette} (ligne {self.numero})"


class Profil(BaseModel):
    model_config = ConfigDict(extra="allow")

    profil: str
    libelle: str = ""
    variables: list[str] = Field(default_factory=list)
    lignes: list[Ligne]
    fichier: str = ""


class Fait(BaseModel):
    """Un fait de décision (`decisions.yaml`, `faits`) : ses valeurs en clair et
    les codes renvoyés par l'API (ou calculés par le démonstrateur) qui y mènent."""

    nom: str
    question: str = ""
    source: str = Field("", description="d'où vient le code : service, champ de la réponse, calcul")
    note: str = ""
    valeurs: dict[str, list[str]] = Field(description="libellé → codes")
    code_inconnu: str | None = Field(None, description="libellé d'un code non listé ; absent : incident")
    sans_code: str | None = Field(None, description="libellé quand le service ne renvoie aucun code retenu")

    @field_validator("valeurs", mode="before")
    @classmethod
    def _valeurs(cls, v: Any) -> Any:
        if isinstance(v, list):  # ancienne forme : liste de libellés
            return {str(x): [] for x in v}
        if isinstance(v, dict):
            return {str(k): ([str(c)] if isinstance(c, str) else [str(x) for x in (c or [])]) for k, c in v.items()}
        return v

    def traduire(self, code: Any) -> str | None:
        """Libellé du fait pour un code d'API (un libellé est accepté tel quel)."""
        if code is None or code == "":
            return self.sans_code
        code = str(code)
        if code in self.valeurs:
            return code
        for libelle, codes in self.valeurs.items():
            if code in codes:
                return libelle
        return self.code_inconnu


class SortieService(BaseModel):
    """Une sortie d'un service d'enrichissement (`services.yaml`)."""

    model_config = ConfigDict(extra="allow")

    libelle: str
    codes: list[str]
    issue: str
    alerte: str | None = None
    effet: str = ""

    @field_validator("codes", mode="before")
    @classmethod
    def _codes(cls, v: Any) -> Any:
        return [v] if isinstance(v, str) else v


class ServiceSorties(BaseModel):
    """Sorties d'un service (`services.yaml`) : code renvoyé → issue du document."""

    nom: str
    libelle: str = ""
    source: str = ""
    note: str = ""
    bloquant: bool = Field(True, description="false : « à reprendre » n'arrête pas la génération")
    sorties: list[SortieService]

    def sortie(self, code: str) -> SortieService | None:
        return next((s for s in self.sorties if code in s.codes or code == s.libelle), None)


Valeurs = str | list[str]


class RegleDecision(BaseModel):
    """Une règle de decisions.yaml, identifiée par son nom en clair.

    `quand` : fait → valeur (ou liste de valeurs admises), toutes requises ;
    `si` : condition rule-engine pour les cas avancés, ajoutée par « et »."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    nom: str
    quand: dict[str, Valeurs] = Field(default_factory=dict)
    si: str | None = None
    alors: str
    profil: str | None = None
    motif: str | None = None
    a_valider: bool = Field(False, alias="à_valider", description="règle encore en discussion")

    @field_validator("nom", mode="before")
    @classmethod
    def _nom(cls, v: Any) -> Any:
        return None if v is None else str(v).strip()

    @field_validator("quand", mode="before")
    @classmethod
    def _quand(cls, v: Any) -> Any:
        if v is None:
            return {}
        if isinstance(v, dict):
            return {str(k): (str(x) if not isinstance(x, list) else [str(y) for y in x]) for k, x in v.items()}
        return v

    @field_validator("si", mode="before")
    @classmethod
    def _si(cls, v: Any) -> Any:
        return None if v is None or str(v).strip() == "" else str(v)

    def admises(self, fait: str) -> list[str]:
        v = self.quand.get(fait)
        return [] if v is None else ([v] if isinstance(v, str) else list(v))

    @property
    def condition(self) -> str:
        """Expression rule-engine équivalente (quand + si)."""
        parties = []
        for fait in self.quand:
            vals = self.admises(fait)
            if len(vals) == 1:
                parties.append(f"{fait} == {_chaine(vals[0])}")
            else:
                parties.append(f"{fait} in [{', '.join(_chaine(x) for x in vals)}]")
        if self.si:
            parties.append(f"({self.si})" if parties else self.si)
        return " and ".join(parties) or "true"

    @property
    def texte_condition(self) -> str:
        """Condition en clair : « type : thèse ; sudoc : rien ou ambigu »."""
        parties = [f"{f} : {' ou '.join(self.admises(f))}" for f in self.quand]
        if self.si:
            parties.append(f"si {self.si}")
        return " ; ".join(parties) or "toujours"


def _chaine(x: str) -> str:
    return '"' + x.replace("\\", "\\\\").replace('"', '\\"') + '"'


class Regles(BaseModel):
    """Règles chargées, avec leur provenance et leur version."""

    variables: dict[str, Variable]
    profils: dict[str, Profil]
    faits: dict[str, Fait]
    services: dict[str, ServiceSorties] = Field(default_factory=dict)
    decisions: list[RegleDecision]
    sinon: dict[str, Any]

    emplacement: str = Field(description="dossier des règles")
    horodatage: datetime
    empreinte: str = Field(description="SHA-256 (12 caractères) des fichiers de règles")
    avertissements: list[str] = Field(default_factory=list)
    textes: dict[str, str] = Field(default_factory=dict, description="chemin relatif → texte YAML lu")

    def actions_sans_profil(self, alors: str) -> bool:
        """Action qui ne produit pas de notice (arrêt, vérification) : pas de profil."""
        from humatheque.regles.moteur import conventions

        return (conventions()["actions"].get(alors) or {}).get("suite") not in ("creer", "convertir")

    @property
    def version(self) -> str:
        return f"{self.horodatage:%Y-%m-%dT%H:%M:%SZ} {self.empreinte}"
