"""Notice produite par le générateur, chaque zone annotée par ses règles (SPEC §8)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class SousZone(BaseModel):
    code: str
    valeur: str
    regle: str


class RegleAppliquee(BaseModel):
    id: str
    statut: str


class Zone(BaseModel):
    etiquette: str
    indicateurs: str | None = Field(None, description="None : zone de contrôle")
    sous_zones: list[SousZone] = Field(default_factory=list)
    regles: list[RegleAppliquee] = Field(default_factory=list)
    libelle: str | None = Field(None, description="Colonne condition : étiquette d'occurrence")

    def ajouter_regle(self, id_: str, statut: str) -> None:
        if not any(r.id == id_ for r in self.regles):
            self.regles.append(RegleAppliquee(id=id_, statut=statut))

    @property
    def controle(self) -> bool:
        return self.indicateurs is None

    def texte(self) -> str:
        """Ligne WinIBW : `ZZZ II$a…` (zone de contrôle : `ZZZ $a…`)."""
        sz = "".join(f"${s.code}{s.valeur}" for s in self.sous_zones)
        return f"{self.etiquette} {self.indicateurs or ''}{sz}"


class RegleNonCouverte(BaseModel):
    regle: str
    zone: str
    statut: str


class Notice(BaseModel):
    profil: str
    version_spec: str
    zones: list[Zone] = Field(default_factory=list)
    alertes: list[str] = Field(default_factory=list)
    non_couvertes: list[RegleNonCouverte] = Field(default_factory=list)
    replis: list[str] = Field(default_factory=list)
    verifications: list[str] = Field(default_factory=list, description="valeurs absentes à vérifier par un catalogueur")
    trace: list[str] = Field(default_factory=list)

    def zones_triees(self) -> list[Zone]:
        return sorted(self.zones, key=lambda z: z.etiquette)  # tri stable

    def zones_de(self, etiquette: str) -> list[Zone]:
        return [z for z in self.zones_triees() if z.etiquette == etiquette]
