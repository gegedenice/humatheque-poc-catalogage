"""Ligne de commande `humatheque`.

Sous-commandes : `serve`, `check-spec`, `run-url`, `run-batch`, `scan-minio`,
`spec-export`, `css`, `cache-reaccentue` (cache du dictionnaire, appelé au
build Docker).
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import stat
import subprocess
import sys
import urllib.request
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


def cache_reaccentue(args: argparse.Namespace) -> int:
    from humatheque.texte_unimarc.ressources import NOM_CACHE, dictionnaire

    d = dictionnaire()
    print(f"Cache {reglages().reaccentue_cache or reglages().dossier_data / NOM_CACHE} : {len(d.formes)} formes, "
          f"{len(d.bigrammes)} bigrammes, {len(d.noms_propres)} noms propres")
    return 0


# Exécutable autonome Tailwind (sans Node), version figée.
TAILWIND_VERSION = "v3.4.17"
TAILWIND_URL = "https://github.com/tailwindlabs/tailwindcss/releases/download/{version}/tailwindcss-{plateforme}"


def _tailwind() -> Path:
    systeme = {"Linux": "linux", "Darwin": "macos", "Windows": "windows"}[platform.system()]
    arch = {"x86_64": "x64", "AMD64": "x64", "arm64": "arm64", "aarch64": "arm64"}[platform.machine()]
    plateforme = f"{systeme}-{arch}" + (".exe" if systeme == "windows" else "")
    chemin = reglages().racine / ".cache" / f"tailwindcss-{TAILWIND_VERSION}-{plateforme}"
    if not chemin.exists():
        chemin.parent.mkdir(parents=True, exist_ok=True)
        url = TAILWIND_URL.format(version=TAILWIND_VERSION, plateforme=plateforme)
        print(f"Téléchargement de {url}")
        urllib.request.urlretrieve(url, chemin)  # noqa: S310 — URL figée
        chemin.chmod(chemin.stat().st_mode | stat.S_IEXEC)
    return chemin


def css(args: argparse.Namespace) -> int:
    racine = reglages().racine
    web = racine / "src" / "humatheque" / "web"
    commande = [str(_tailwind()), "-c", str(web / "tailwind" / "tailwind.config.js"),
                "-i", str(web / "tailwind" / "input.css"), "-o", str(web / "static" / "css" / "app.css")]
    commande.append("--watch" if args.watch else "--minify")
    return subprocess.call(commande, cwd=racine)


def serve(args: argparse.Namespace) -> int:
    import uvicorn

    from humatheque.web import create_app

    uvicorn.run(create_app(planificateur=True), host=args.host, port=args.port)
    return 0


def _traiter_documents(documents: list[list[str]], args: argparse.Namespace) -> int:
    """Lot exécuté dans le terminal (exécuteur synchrone), puis résumé."""
    from humatheque.application import Application, EntreeLot, ExecuteurSynchrone

    app = Application(executeur=ExecuteurSynchrone())
    lot = app.creer_lot(EntreeLot(type_document=args.type, fournisseur=args.fournisseur, documents=documents,
                                  source="url"))
    vue = app.vue_lot(lot.id)
    for ligne in app.store.lignes_du_lot(lot.id):
        d = app.vue_dossier(ligne.id).dossier
        print(f"{d.document.id} → {d.routage}" + (f" ({d.motif_arret})" if d.motif_arret else ""))
        if args.notice and (d.notice_texte or d.notice_convertie):
            print(d.notice_texte or d.notice_convertie.texte, end="\n\n")
    i = vue.indicateurs
    print(f"Lot {lot.id} : {i.documents} document(s), {i.passes_seuls} passé(s) seul(s), {i.en_attente} à reprendre")
    return 0


def run_url(args: argparse.Namespace) -> int:
    return _traiter_documents([args.urls] if args.un_document else [[u] for u in args.urls], args)


def run_batch(args: argparse.Namespace) -> int:
    from humatheque.application import documents_depuis_liste

    return _traiter_documents(documents_depuis_liste(Path(args.fichier).read_text(encoding="utf-8")), args)


def scan_minio(args: argparse.Namespace) -> int:
    """Dossiers du bucket et dernier statut (ING-F-005) ; --lancer-nouveaux traite les nouveaux."""
    from humatheque.application import Application, ExecuteurSynchrone

    app = Application(executeur=ExecuteurSynchrone())
    vue = app.balayer("manuel")
    if not vue.configure:
        print("Stockage S3 non configuré (MINIO_ENDPOINT, MINIO_BUCKET, MINIO_ACCESS_KEY, MINIO_SECRET_KEY).",
              file=sys.stderr)
        return 2
    if vue.erreur:
        print(f"ERREUR : {vue.erreur}", file=sys.stderr)
        return 2
    for l in vue.dossiers:
        d = l.document
        statut = "nouveau" if l.nouveau else (l.routage or l.etat or "")
        print(f"{d.id:<30} {d.type_document or '?':<8} {len(d.images):>3} image(s)  {statut}"
              + (f"  [{d.invalide}]" if d.invalide else ""))
    nouveaux = [l.document.id for l in vue.dossiers if l.nouveau]
    print(f"{len(vue.dossiers)} dossier(s) dans {vue.bucket}/{vue.prefixe}, {len(nouveaux)} nouveau(x)")
    if args.lancer_nouveaux and nouveaux:
        lot = app.lancer_selection(nouveaux, args.fournisseur)
        i = app.vue_lot(lot.id).indicateurs
        print(f"Lot {lot.id} : {i.passes_seuls} passé(s) seul(s), {i.en_attente} à reprendre")
    return 0


def spec_export(args: argparse.Namespace) -> int:
    """Instantané des tables Grist dans spec/ (+ copie horodatée dans spec/archives/)."""
    from humatheque.spec.export import exporter
    from humatheque.spec.grist import GristInjoignable

    try:
        r = exporter(reglages(), args.dossier)
    except (GristInjoignable, SpecIntrouvable) as e:
        print(f"ERREUR : {e}", file=sys.stderr)
        return 2
    for table, n in r["tables"].items():
        print(f"  {table:<18} {n} ligne(s)")
    print(f"Instantané {r['horodatage']} (empreinte {r['empreinte']}) ; archive {r['archive']}")
    return 0


def construire_parseur() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="humatheque", description="Démonstrateur de catalogage assisté")
    sous = p.add_subparsers(dest="commande", required=True)

    c = sous.add_parser("check-spec", help="vérifie la spécification (Regles, Sources, Situations…)")
    c.add_argument("--source", choices=["csv", "grist"], default=None,
                   help=f"source de la spec (défaut : SPEC_SOURCE={reglages().spec_source})")
    c.add_argument("--dossier", type=Path, default=None, help="dossier des CSV (défaut : spec/)")
    c.add_argument("--json", action="store_true", help="rapport au format JSON")
    c.set_defaults(fonction=check_spec)

    c = sous.add_parser("cache-reaccentue", help="construit le cache du dictionnaire reaccentue dans data/")
    c.set_defaults(fonction=cache_reaccentue)

    c = sous.add_parser("serve", help="lance l'interface web (port 8000)")
    c.add_argument("--host", default=os.getenv("HOST", "127.0.0.1"))
    c.add_argument("--port", type=int, default=8000)
    c.set_defaults(fonction=serve)

    c = sous.add_parser("spec-export", help="écrit un instantané des tables Grist dans spec/")
    c.add_argument("--dossier", type=Path, default=None, help="dossier cible (défaut : spec/)")
    c.set_defaults(fonction=spec_export)

    c = sous.add_parser("scan-minio", help="liste les dossiers du bucket S3 et leur dernier statut")
    c.add_argument("--lancer-nouveaux", action="store_true", help="traite les dossiers jamais traités")
    c.add_argument("--fournisseur", default=None)
    c.set_defaults(fonction=scan_minio)

    c = sous.add_parser("css", help="compile la feuille Tailwind (exécutable autonome, sans Node)")
    c.add_argument("--watch", action="store_true", help="recompile à chaque modification")
    c.set_defaults(fonction=css)

    for nom, aide in (("run-url", "traite une ou plusieurs URL d'images"),
                      ("run-batch", "traite un fichier d'URL (une par ligne, format minio_urls.txt)")):
        c = sous.add_parser(nom, help=aide)
        if nom == "run-url":
            c.add_argument("urls", nargs="+")
            c.add_argument("--un-document", action="store_true", help="les URL forment un seul document")
            c.set_defaults(fonction=run_url)
        else:
            c.add_argument("fichier")
            c.set_defaults(fonction=run_batch)
        c.add_argument("--type", choices=["these", "memoire"], default="these")
        c.add_argument("--fournisseur", default=None)
        c.add_argument("--notice", action="store_true", help="affiche les notices produites")
    return p


def main(argv: list[str] | None = None) -> int:
    args = construire_parseur().parse_args(argv)
    return args.fonction(args)


if __name__ == "__main__":
    raise SystemExit(main())
