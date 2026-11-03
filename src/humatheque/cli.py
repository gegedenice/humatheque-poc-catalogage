"""Ligne de commande `humatheque`.

Sous-commandes disponibles à ce stade : `check-spec`. Les autres
(`serve`, `run-url`, `run-batch`, `scan-minio`, `spec-export`) arrivent avec
les étapes suivantes (SPEC §11).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from humatheque.config import reglages
from humatheque.spec import SpecIntrouvable, charger_spec, verifier
from humatheque.spec.verificateur import Rapport


def _afficher(rapport: Rapport) -> None:
    print(f"Spécification : {rapport.source} — {rapport.emplacement}")
    print(f"Version       : {rapport.version}")
    for c in sorted(rapport.constats, key=lambda c: (c.gravite != "erreur", c.table, c.ligne)):
        marque = "ERREUR" if c.gravite == "erreur" else "avert."
        print(f"  [{marque}] {c.table} {c.ligne} ({c.code}) : {c.message}")
    print(f"Bilan : {len(rapport.erreurs)} erreur(s) bloquante(s), {len(rapport.avertissements)} avertissement(s)")


def check_spec(args: argparse.Namespace) -> int:
    try:
        spec = charger_spec(source=args.source, dossier=args.dossier)
    except SpecIntrouvable as e:
        print(f"ERREUR : {e}", file=sys.stderr)
        return 2
    rapport = verifier(spec)
    if args.json:
        print(json.dumps(rapport.model_dump(), ensure_ascii=False, indent=2))
    else:
        _afficher(rapport)
    return 1 if rapport.bloquant else 0


def construire_parseur() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="humatheque", description="Démonstrateur de catalogage assisté")
    sous = p.add_subparsers(dest="commande", required=True)

    c = sous.add_parser("check-spec", help="vérifie la spécification (Regles, Sources, Situations…)")
    c.add_argument("--source", choices=["csv", "grist"], default=None,
                   help=f"source de la spec (défaut : SPEC_SOURCE={reglages().spec_source})")
    c.add_argument("--dossier", type=Path, default=None, help="dossier des CSV (défaut : spec/)")
    c.add_argument("--json", action="store_true", help="rapport au format JSON")
    c.set_defaults(fonction=check_spec)
    return p


def main(argv: list[str] | None = None) -> int:
    args = construire_parseur().parse_args(argv)
    return args.fonction(args)


if __name__ == "__main__":
    raise SystemExit(main())
