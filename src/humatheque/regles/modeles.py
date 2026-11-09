"""Modèles des règles de catalogage (SPEC §7, §8) : variables, pipeline (étapes et
arbre de décision sur les codes renvoyés par les API), notice structurée (socle + variantes).

Les fichiers YAML sont validés à la lecture ; aucune expression : les
conditions sont des nœuds d'arbre, les zones des objets configurables.
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


# ------------------------------------------------------------------ pipeline (étapes et arbre de décision)
Resultat = Literal["stop", "stop à reprendre", "conversion", "poursuite", "poursuite avec alerte",
                   "poursuite à reprendre", "création"]
RESULTATS: dict[str, str] = {
    "stop": "le traitement s'arrête : rien à faire (terminé)",
    "stop à reprendre": "le traitement s'arrête pour une vérification manuelle (à reprendre)",
    "conversion": "la notice Sudoc trouvée est convertie (notice cible), puis le traitement s'arrête (terminé)",
    "poursuite": "le traitement passe à l'étape suivante",
    "poursuite avec alerte": "le traitement continue ; une alerte est ajoutée au dossier",
    "poursuite à reprendre": "le traitement continue ; le document sera à reprendre (vérification manuelle)",
    "création": "la notice est créée (notice cible)",
}
Valeurs = str | list[str]


def _valeurs(v: Any) -> Any:
    if v is None:
        return {}
    if isinstance(v, dict):
        return {str(k): (str(x) if not isinstance(x, list) else [str(y) for y in x]) for k, x in v.items()}
    return v


class Noeud(BaseModel):
    """Un nœud de l'arbre : conditions (`si` : ET entre variables, OU entre valeurs ;
    `sauf` : valeurs exclues), puis soit des sous-branches, soit un résultat."""

    model_config = ConfigDict(extra="forbid")

    si: dict[str, Valeurs] = Field(default_factory=dict)
    sauf: dict[str, Valeurs] = Field(default_factory=dict)
    branches: list[Noeud] = Field(default_factory=list)
    sinon: Noeud | None = Field(None, description="aucune sous-branche ne correspond")
    resultat: Resultat | None = None
    notice: str | None = Field(None, description="conversion, création : notice cible (variante)")
    motif: str | None = None
    alerte: str | None = Field(None, description="poursuite avec alerte : texte de l'alerte")

    _si = field_validator("si", "sauf", mode="before")(classmethod(lambda cls, v: _valeurs(v)))

    @staticmethod
    def liste(v: Valeurs) -> list[str]:
        return [v] if isinstance(v, str) else list(v)

    def correspond(self, valeurs: dict[str, Any]) -> bool:
        def egal(var: str) -> Any:
            x = valeurs.get(var)
            return None if x is None else str(x)
        return (all(egal(k) in self.liste(v) for k, v in self.si.items())
                and not any(egal(k) in self.liste(v) for k, v in self.sauf.items()))

    @property
    def condition(self) -> str:
        """Condition en clair : « status = a OU b ET EPHE = oui SAUF … »."""
        parties = [f"{k} = {' OU '.join(self.liste(v))}" for k, v in self.si.items()]
        parties += [f"SAUF {k} = {' OU '.join(self.liste(v))}" for k, v in self.sauf.items()]
        return " ET ".join(parties) or "toujours"

    @property
    def texte_resultat(self) -> str:
        if not self.resultat:
            return ""
        r = self.resultat.upper() if self.resultat in ("stop", "poursuite", "conversion", "création") else \
            self.resultat.replace("stop", "STOP").replace("poursuite", "POURSUITE")
        if self.notice:
            r += f" {self.notice}" + (" puis STOP" if self.resultat == "conversion" else "")
        return r


class EtapePipeline(BaseModel):
    """Une étape du pipeline : variables lues (codes renvoyés par l'API) et arbre."""

    model_config = ConfigDict(extra="forbid")

    id: str
    libelle: str
    source: str = ""
    variables: dict[str, list[str]] = Field(default_factory=dict, description="variable → valeurs possibles")
    branches: list[Noeud] = Field(default_factory=list)
    sinon: Noeud = Field(default_factory=lambda: Noeud(resultat="stop à reprendre", motif="cas non prévu"))
    note: str = ""


class Regles(BaseModel):
    """Règles chargées, avec leur provenance et leur version."""

    variables: dict[str, Variable]
    communes: dict[str, list[str]] = Field(default_factory=dict, description="variables lues à toutes les étapes")
    etapes: dict[str, EtapePipeline] = Field(default_factory=dict, description="pipeline, dans l'ordre")
    socle: Socle
    variantes: dict[str, Variante] = Field(default_factory=dict)

    emplacement: str = Field(description="dossier des règles")
    horodatage: datetime
    empreinte: str = Field(description="SHA-256 (12 caractères) des fichiers de règles")
    avertissements: list[str] = Field(default_factory=list)
    textes: dict[str, str] = Field(default_factory=dict, description="chemin relatif → texte YAML lu")

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


def resoudre(socle: Socle, v: Variante) -> NoticeModele:
    """Socle + variante : retirer, remplacer en place, ajouter à la fin (tri par étiquette à la sortie)."""
    remplacements = {z.id: z.model_copy(update={"origine": v.variante}) for z in v.remplacer}
    zones = [remplacements.get(z.id, z) for z in socle.zones if z.id not in set(v.retirer)]
    zones += [z.model_copy(update={"origine": v.variante}) for z in v.ajouter]
    return NoticeModele(profil=v.variante, libelle=v.libelle, fichier=v.fichier, zones=zones, rendus=socle.rendus)

Regles.model_rebuild()
