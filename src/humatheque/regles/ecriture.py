"""Réécriture de `decisions.yaml` depuis le tableau de décision (page Règles).

Seules les sections `regles:` et `sinon:` sont régénérées : l'en-tête, le
dictionnaire des faits et leurs commentaires sont conservés tels quels.
"""

from __future__ import annotations

from typing import Any

import yaml

from humatheque.regles.modeles import RegleDecision


def _scalaire(v: Any) -> str:
    return yaml.safe_dump(v, allow_unicode=True, default_flow_style=True, width=10_000).strip().removesuffix("\n...").removesuffix("...").strip()


def _flux(d: dict[str, Any]) -> str:
    return yaml.safe_dump(d, allow_unicode=True, default_flow_style=True, sort_keys=False, width=10_000).strip()


def ecrire_regle(r: RegleDecision) -> str:
    lignes = [f"  - nom: {_scalaire(r.nom)}"]
    if r.quand:
        lignes.append(f"    quand: {_flux(r.quand)}")
    lignes.append(f"    alors: {_scalaire(r.alors)}")
    if r.profil:
        lignes.append(f"    profil: {_scalaire(r.profil)}")
    if r.verification:
        lignes.append(f"    verification: {_scalaire(r.verification)}")
    if r.motif:
        lignes.append(f"    motif: {_scalaire(r.motif)}")
    if r.a_valider:
        lignes.append("    à_valider: true")
    return "\n".join(lignes)


def remplacer_regles(texte: str, regles: list[RegleDecision], sinon: dict[str, Any]) -> str:
    """Texte de decisions.yaml avec `regles:` et `sinon:` régénérés, le reste conservé."""
    lignes = texte.splitlines()
    debut = next((i for i, l in enumerate(lignes) if l.startswith("regles:")), None)
    fin = next((i for i, l in enumerate(lignes) if l.startswith("sinon:")), None)
    tete = lignes[:debut] if debut is not None else lignes
    queue: list[str] = []
    if fin is not None:  # ce qui suit la section sinon (autres clés éventuelles)
        j = fin + 1
        while j < len(lignes) and (lignes[j].startswith((" ", "\t")) or not lignes[j].strip()):
            j += 1
        queue = lignes[j:]
    corps = "\n\n".join(ecrire_regle(r) for r in regles)
    sinon_txt = f"sinon: {_flux({k: v for k, v in sinon.items() if v not in (None, '')})}"
    sortie = "\n".join(tete).rstrip() + "\n\nregles:\n" + corps + "\n\n" + sinon_txt + "\n"
    if queue:
        sortie += "\n" + "\n".join(queue).rstrip() + "\n"
    return sortie
