"""Modèles des règles de catalogage (SPEC §8) : variables, faits et décisions,
sorties des services, vérifications humaines, notice structurée (socle + variantes).

Les fichiers YAML sont validés à la lecture ; aucune expression : les
conditions sont des tables (`quand`), les zones des objets configurables.
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

    `quand` : fait → valeur (ou liste de valeurs admises), toutes requises ; un
    fait non cité est indifférent."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    nom: str
    quand: dict[str, Valeurs] = Field(default_factory=dict)
    alors: str
    profil: str | None = None
    motif: str | None = None
    verification: str | None = Field(None, description="état de vérification (action vérification)")
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

    def admises(self, fait: str) -> list[str]:
        v = self.quand.get(fait)
        return [] if v is None else ([v] if isinstance(v, str) else list(v))

    def correspond(self, faits: dict[str, Any]) -> bool:
        """Chaque fait cité a l'une des valeurs admises."""
        return all(faits.get(f) in self.admises(f) for f in self.quand)

    @property
    def texte_condition(self) -> str:
        """Condition en clair : « type : thèse ; sudoc : rien ou ambigu »."""
        parties = [f"{f} : {' ou '.join(self.admises(f))}" for f in self.quand]
        return " ; ".join(parties) or "toujours"


class Regles(BaseModel):
    """Règles chargées, avec leur provenance et leur version."""

    variables: dict[str, Variable]
    socle: Socle
    variantes: dict[str, Variante] = Field(default_factory=dict)
    verifications: dict[str, EtatVerification] = Field(default_factory=dict)
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
    def profils(self) -> dict[str, "NoticeModele"]:
        """Notices-modèles résolues : socle + chaque variante."""
        return {nom: resoudre(self.socle, v) for nom, v in self.variantes.items()}

    @property
    def version(self) -> str:
        return f"{self.horodatage:%Y-%m-%dT%H:%M:%SZ} {self.empreinte}"


# ------------------------------------------------------------------ notice structurée (socle + variantes)
SiAbsente = Literal["omettre", "repli", "verification"]


class Partie(BaseModel):
    """Morceau d'une valeur composée : texte fixe ou variable."""

    texte: str | None = None
    variable: str | None = None
    attribut: str | None = None


class SousZoneRegle(BaseModel):
    """Une sous-zone configurable. Sa valeur est d'un seul genre : constante,
    variable, composition (parties) ou autorité (rendu PPN / nom-prénom)."""

    model_config = ConfigDict(extra="forbid")

    code: str
    libelle: str = ""
    constante: str | None = None
    variable: str | None = None
    attribut: str | None = Field(None, description="forme (défaut), nom, prenom, ppn")
    parties: list[Partie] | None = Field(None, description="composition de textes et de variables")
    autorite: str | None = Field(None, description="variable personne / organisme rendue selon `rendu`")
    rendu: str | None = Field(None, description="mode de rendu d'autorité (défaut : celui du type)")
    prefixe: str = ""
    suffixe: str = ""
    liste: Literal["repeter", "joindre"] = Field("repeter", description="variable liste : une sous-zone par "
                                                 "valeur, ou valeurs jointes")
    separateur: str = ", "
    dernier: str | None = Field(None, description="séparateur avant la dernière valeur (défaut : separateur)")
    si_absente: SiAbsente = "omettre"
    repli: str | None = None
    remarque: str | None = None

    @field_validator("code", "constante", "repli", mode="before")
    @classmethod
    def _chaine(cls, v: Any) -> Any:
        return None if v is None else str(v)

    @property
    def genre(self) -> str:
        if self.autorite:
            return "autorite"
        if self.parties:
            return "composition"
        if self.variable:
            return "variable"
        if self.constante is not None:
            return "constante"
        return "vide"

    def variables(self) -> list[str]:
        if self.autorite:
            return [self.autorite]
        if self.parties:
            return [p.variable for p in self.parties if p.variable]
        return [self.variable] if self.variable else []

    @property
    def presence(self) -> str:
        """Résumé lisible : toujours, si renseignée, repli, vérification."""
        if self.genre == "constante":
            return "toujours"
        if self.si_absente == "repli":
            return f"repli « {self.repli} » si absente"
        if self.si_absente == "verification":
            return "vérification si absente"
        return "si renseignée"


class ZoneRegle(BaseModel):
    """Une zone de notice : étiquette, indicateurs, sous-zones configurables."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: str
    zone: str
    libelle: str = ""
    indicateurs: str | None = Field(None, description="2 caractères ; absent : zone de contrôle")
    sous_zones: list[SousZoneRegle] = Field(default_factory=list)
    priorite: int | None = None
    a_valider: bool = Field(False, alias="à_valider")
    actif: bool = Field(True, description="false : zone documentée mais non produite")
    remarque: str | None = None
    origine: str = Field("", description="socle ou nom de la variante (renseigné au chargement)")

    @field_validator("id", "zone", "indicateurs", mode="before")
    @classmethod
    def _chaine(cls, v: Any) -> Any:
        return None if v is None else str(v)

    @property
    def etiquette(self) -> str:
        return self.zone

    @property
    def statut(self) -> str:
        return "à valider" if self.a_valider else "validée"

    @property
    def trace(self) -> str:
        return f"{self.id} ({self.origine})" if self.origine else self.id

    def variables(self) -> list[str]:
        return [v for sz in self.sous_zones for v in sz.variables()]

    @property
    def iteree(self) -> str | None:
        """Variable d'autorité de la zone : une zone par élément."""
        return next((sz.autorite for sz in self.sous_zones if sz.autorite), None)

    def apercu(self) -> str:
        """Forme compacte, façon WinIBW, pour les listes : 200 1#$a‹titre_classement›…"""
        morceaux = []
        for sz in self.sous_zones:
            if sz.genre == "constante":
                v = sz.constante
            elif sz.genre == "autorite":
                v = f"‹{sz.autorite} : {sz.rendu or 'rendu du type'}›"
                morceaux.append(f"{v}")
                continue
            elif sz.genre == "composition":
                v = "".join(p.texte or f"‹{p.variable}›" for p in sz.parties or [])
            elif sz.genre == "variable":
                v = f"‹{sz.variable}{'.' + sz.attribut if sz.attribut else ''}›"
            else:
                v = ""
            morceaux.append(f"${sz.code}{sz.prefixe}{v}{sz.suffixe}")
        return f"{self.zone} {self.indicateurs or ''}{''.join(morceaux)}"


class Variante(BaseModel):
    """Variante d'une notice : modifications ciblées du socle."""

    model_config = ConfigDict(extra="forbid")

    variante: str
    libelle: str = ""
    description: str = ""
    ajouter: list[ZoneRegle] = Field(default_factory=list)
    remplacer: list[ZoneRegle] = Field(default_factory=list)
    retirer: list[str] = Field(default_factory=list)
    fichier: str = ""


class Socle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rendus: dict[str, str] = Field(default_factory=dict, description="type (personne, organisme) → mode de rendu")
    zones: list[ZoneRegle]
    fichier: str = ""


class NoticeModele(BaseModel):
    """Notice-modèle résolue : socle + variante (ce que choisit une décision)."""

    profil: str
    libelle: str = ""
    fichier: str = ""
    zones: list[ZoneRegle]
    rendus: dict[str, str] = Field(default_factory=dict)

    @property
    def variables(self) -> list[str]:
        return list(dict.fromkeys(v for z in self.zones if z.actif for v in z.variables()))


# ------------------------------------------------------------------ vérifications humaines (états)
class ChoixVerification(BaseModel):
    """Un choix proposé au catalogueur : corriger des faits (puis nouvelle décision)
    ou imposer une action (avec profil)."""

    model_config = ConfigDict(extra="forbid")

    libelle: str
    faits: dict[str, str] = Field(default_factory=dict)
    action: str | None = None
    profil: str | None = None
    description: str = ""


class EtatVerification(BaseModel):
    """État « en attente de vérification » : question posée et choix possibles."""

    model_config = ConfigDict(extra="forbid")

    nom: str
    libelle: str
    question: str = ""
    montrer: str | None = Field(None, description="candidats affichés : alma, sudoc")
    choix: list[ChoixVerification]


def resoudre(socle: Socle, v: Variante) -> NoticeModele:
    """Socle + variante : retirer, remplacer en place, ajouter à la fin (tri par étiquette à la sortie)."""
    remplacements = {z.id: z.model_copy(update={"origine": v.variante}) for z in v.remplacer}
    zones = [remplacements.get(z.id, z) for z in socle.zones if z.id not in set(v.retirer)]
    zones += [z.model_copy(update={"origine": v.variante}) for z in v.ajouter]
    return NoticeModele(profil=v.variante, libelle=v.libelle, fichier=v.fichier, zones=zones, rendus=socle.rendus)
