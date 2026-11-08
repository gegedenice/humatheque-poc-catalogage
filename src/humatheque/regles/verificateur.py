"""Vérificateur des règles (`humatheque check-spec`, SPEC §8.4), d'après
`reference/moteur-regles/moteur.py`. Signale sans rien corriger.

`verifier_textes` vérifie aussi des brouillons (interface d'administration) :
un YAML illisible donne des constats d'erreur au lieu d'une exception.
"""

from __future__ import annotations

import itertools
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel

from humatheque.regles.chargement import ReglesInvalides, charger_regles
from humatheque.regles.modeles import Regles
from humatheque.regles.modeles import RegleDecision, ZoneRegle
from humatheque.regles.moteur import choisir, conventions
from humatheque.regles.notice import RENDUS

Gravite = Literal["erreur", "avertissement", "info"]


class Constat(BaseModel):
    gravite: Gravite
    code: str
    table: str = ""     # fichier (profils/…, decisions.yaml…)
    ligne: str = ""     # lieu dans le fichier
    message: str


class Rapport(BaseModel):
    emplacement: str
    version: str
    constats: list[Constat]

    @property
    def erreurs(self) -> list[Constat]:
        return [c for c in self.constats if c.gravite == "erreur"]

    @property
    def avertissements(self) -> list[Constat]:
        return [c for c in self.constats if c.gravite == "avertissement"]

    @property
    def bloquant(self) -> bool:
        return bool(self.erreurs)


def _zone(regles: Regles, z: ZoneRegle, fichier: str, attributs: dict[str, set[str]], signaler) -> set[str]:
    """Contrôles d'une zone ; renvoie les variables utilisées (zone active)."""
    lieu = f"zone {z.id}"
    utilisees: set[str] = set()
    if not (len(z.zone) == 3 and z.zone.isdigit()):
        signaler("erreur", "zone_illisible", fichier, lieu, "l'étiquette doit compter 3 chiffres")
    if z.indicateurs is not None and len(z.indicateurs) != 2:
        signaler("erreur", "indicateurs_invalides", fichier, lieu, "les indicateurs comptent 2 caractères")
    autorites = [sz.autorite for sz in z.sous_zones if sz.autorite]
    if len(set(autorites)) > 1:
        signaler("erreur", "repetition_multiple", fichier, lieu,
                 f"une zone ne se répète que sur une autorité ({', '.join(sorted(set(autorites)))})")
    for sz in z.sous_zones:
        ou = f"{lieu} ${sz.code}"
        genres = [g for g, v in (("constante", sz.constante is not None), ("variable", sz.variable),
                                 ("composition", sz.parties), ("autorite", sz.autorite)) if v]
        if len(genres) > 1:
            signaler("erreur", "valeur_ambigue", fichier, ou, f"une seule sorte de valeur ({', '.join(genres)})")
        if not genres and z.actif:
            signaler("erreur", "valeur_absente", fichier, ou, "sous-zone sans valeur dans une zone active")
        if len(sz.code) != 1:
            signaler("erreur", "code_invalide", fichier, ou, "le code de sous-zone est un caractère")
        if sz.si_absente == "repli" and sz.repli is None:
            signaler("erreur", "repli_absent", fichier, ou, "« valeur de repli » choisie sans valeur")
        if sz.rendu and sz.rendu not in RENDUS:
            signaler("erreur", "rendu_inconnu", fichier, ou, f"mode de rendu « {sz.rendu} » inconnu")
        noms = [(sz.variable, sz.attribut)] if sz.variable else []
        noms += [(p.variable, p.attribut) for p in sz.parties or [] if p.variable]
        noms += [(sz.autorite, None)] if sz.autorite else []
        for n, a in noms:
            v = regles.variables.get(n)
            if v is None:
                signaler("erreur", "variable_inconnue", fichier, ou, f"variable inconnue « {n} »")
                continue
            if z.actif:
                utilisees.add(n)
            if a and a not in attributs.get(v.type, {"forme"}):
                signaler("erreur", "attribut_impossible", fichier, ou, f"attribut .{a} impossible pour {n} ({v.type})")
            if n == sz.autorite and v.type not in attributs:
                signaler("erreur", "autorite_invalide", fichier, ou, f"{n} n'est ni une personne ni un organisme")
    return utilisees


def verifier(regles: Regles) -> Rapport:
    conv = conventions()
    attributs = {t: set(a) for t, a in conv["attributs"].items()}
    constats: list[Constat] = []

    def signaler(gravite: Gravite, code: str, fichier: str, lieu: str, message: str) -> None:
        constats.append(Constat(gravite=gravite, code=code, table=fichier, ligne=lieu, message=message))

    # --- notice : socle et variantes
    for t, mode in regles.socle.rendus.items():
        if mode not in RENDUS:
            signaler("erreur", "rendu_inconnu", regles.socle.fichier, "rendus", f"{t} : mode « {mode} » inconnu")
    ids = [z.id for z in regles.socle.zones]
    for i in {i for i in ids if ids.count(i) > 1}:
        signaler("erreur", "zone_doublon", regles.socle.fichier, f"zone {i}", f"identifiant « {i} » en double")
    utilisees = set()
    for z in regles.socle.zones:
        utilisees |= _zone(regles, z, regles.socle.fichier, attributs, signaler)
    for nom, v in regles.variantes.items():
        for z in v.ajouter + v.remplacer:
            utilisees |= _zone(regles, z, v.fichier, attributs, signaler)
        for z in v.remplacer:
            if z.id not in ids:
                signaler("erreur", "zone_inconnue", v.fichier, f"zone {z.id}", "remplace une zone absente du socle")
        for i in v.retirer:
            if i not in ids:
                signaler("erreur", "zone_inconnue", v.fichier, f"zone {i}", "retire une zone absente du socle")
        for z in v.ajouter:
            if z.id in ids:
                signaler("erreur", "zone_doublon", v.fichier, f"zone {z.id}", "ajoute une zone déjà dans le socle (remplacer ?)")
    for n in sorted(set(regles.variables) - utilisees):
        signaler("info", "variable_inutilisee", "variables.yaml", n, f"{n} utilisée par aucune zone active")

    # --- vérifications humaines
    for nom, e in regles.verifications.items():
        for c in e.choix:
            ou = f"{nom} / {c.libelle}"
            if bool(c.faits) == bool(c.action):
                signaler("erreur", "choix_invalide", "verifications.yaml", ou, "un choix corrige des faits OU impose une action")
            for f, v in c.faits.items():
                if f not in regles.faits or v not in regles.faits[f].valeurs:
                    signaler("erreur", "valeur_inconnue", "verifications.yaml", ou, f"{f} = « {v} » absent du dictionnaire")
            if c.action and c.action not in conv["actions"]:
                signaler("erreur", "action_inconnue", "verifications.yaml", ou, f"action « {c.action} » inconnue")
            elif c.action and conv["actions"][c.action]["suite"] in ("creer", "convertir") \
                    and c.profil not in regles.variantes:
                signaler("erreur", "profil_inconnu", "verifications.yaml", ou, f"profil inconnu {c.profil}")

    # --- décisions
    vus: set[str] = set()
    for r in regles.decisions:
        lieu = f"« {r.nom} »"
        if r.nom in vus:
            signaler("erreur", "decision_doublon", "decisions.yaml", lieu, f"nom « {r.nom} » en double")
        vus.add(r.nom)
        for fait in r.quand:
            if fait not in regles.faits:
                signaler("erreur", "fait_inconnu", "decisions.yaml", lieu, f"fait « {fait} » absent du dictionnaire")
                continue
            for v in r.admises(fait):
                if v not in regles.faits[fait].valeurs:
                    signaler("erreur", "valeur_inconnue", "decisions.yaml", lieu,
                             f"{fait} : valeur « {v} » inconnue (valeurs : {', '.join(regles.faits[fait].valeurs)})")
        if not r.quand:
            signaler("avertissement", "regle_toujours_vraie", "decisions.yaml", lieu,
                     "aucun fait dans « quand » : la règle s'applique toujours")
        if r.verification and r.verification not in regles.verifications:
            signaler("erreur", "verification_inconnue", "decisions.yaml", lieu,
                     f"état de vérification « {r.verification} » absent de verifications.yaml")
        if r.alors == "vérification" and not r.verification:
            signaler("avertissement", "verification_sans_etat", "decisions.yaml", lieu,
                     "vérification sans état : aucun choix ne sera proposé au catalogueur")
        if r.alors not in conv["actions"]:
            signaler("erreur", "action_inconnue", "decisions.yaml", lieu, f"action « {r.alors} » inconnue")
        elif conv["actions"][r.alors]["suite"] in ("creer", "convertir") and r.profil not in regles.variantes:
            signaler("erreur", "profil_inconnu", "decisions.yaml", lieu, f"profil inconnu {r.profil}")
    if regles.sinon.get("verification") and regles.sinon["verification"] not in regles.verifications:
        signaler("erreur", "verification_inconnue", "decisions.yaml", "sinon",
                 f"état de vérification « {regles.sinon['verification']} » absent de verifications.yaml")
    if regles.sinon.get("alors") and regles.sinon["alors"] not in conv["actions"]:
        signaler("erreur", "action_inconnue", "decisions.yaml", "sinon", f"action « {regles.sinon['alors']} » inconnue")
    atteints = {r.profil for r in regles.decisions}
    atteints |= {c.profil for e in regles.verifications.values() for c in e.choix}
    for p in sorted(set(regles.variantes) - atteints):
        signaler("avertissement", "profil_non_atteint", regles.variantes[p].fichier, "", f"variante {p} atteinte par aucune décision")

    # --- dictionnaire des faits : un code ne mène qu'à une valeur
    for nom, f in regles.faits.items():
        vus_codes: dict[str, str] = {}
        for valeur, codes in f.valeurs.items():
            for c in codes:
                if c in vus_codes and vus_codes[c] != valeur:
                    signaler("erreur", "code_ambigu", "decisions.yaml", f"fait {nom}",
                             f"code « {c} » listé pour « {vus_codes[c]} » et « {valeur} »")
                vus_codes[c] = valeur
        for cle in ("code_inconnu", "sans_code"):
            v = getattr(f, cle)
            if v is not None and v not in f.valeurs:
                signaler("erreur", "valeur_inconnue", "decisions.yaml", f"fait {nom}", f"{cle} : « {v} » n'est pas une valeur")

    # --- sorties des services
    from humatheque import issues

    for nom, svc in regles.services.items():
        vus_codes = {}
        for so in svc.sorties:
            if so.issue not in issues.ORDRE:
                signaler("erreur", "issue_inconnue", "services.yaml", f"{nom} / {so.libelle}",
                         f"issue « {so.issue} » inconnue ({', '.join(issues.ORDRE)})")
            for c in so.codes:
                if c in vus_codes:
                    signaler("erreur", "code_ambigu", "services.yaml", nom,
                             f"code « {c} » listé pour « {vus_codes[c]} » et « {so.libelle} »")
                vus_codes[c] = so.libelle

    # --- complétude
    comp = conv["completude"]
    noms = [n for n in comp["faits"] if n in regles.faits]
    for combo in itertools.product(*(list(regles.faits[n].valeurs) for n in noms)):
        faits = {**dict(zip(noms, combo)), **comp.get("fixes", {})}
        if any(RegleDecision(nom="_", alors="_", quand=q).correspond(faits) for q in comp.get("incompatibles") or []):
            continue
        if choisir(regles, faits).regle["nom"] == "sinon":
            signaler("avertissement", "cas_non_couvert", "decisions.yaml", "",
                     "cas non couvert : " + ", ".join(f"{k} = {v}" for k, v in faits.items()))

    for a in regles.avertissements:
        signaler("avertissement", "chargement", "", "", a)

    return Rapport(emplacement=regles.emplacement, version=regles.version, constats=constats)


def rapport_invalide(e: ReglesInvalides, emplacement: str) -> Rapport:
    return Rapport(emplacement=emplacement, version="—", constats=[
        Constat(gravite="erreur", code="yaml_invalide", table=f, ligne=l, message=m) for f, l, m in e.problemes])


def verifier_textes(brouillons: dict[str, str], reglages: Any = None, dossier: Path | None = None) -> Rapport:
    """Vérifie les règles du dossier avec certains fichiers remplacés par des brouillons."""
    try:
        regles = charger_regles(reglages, dossier=dossier, brouillons=brouillons)
    except ReglesInvalides as e:
        return rapport_invalide(e, str(dossier or ""))
    return verifier(regles)
