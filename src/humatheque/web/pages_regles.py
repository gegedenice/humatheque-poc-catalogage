"""Pages d'administration des règles (SPEC §9.3, `/admin/regles`) : consultation,
banc d'essai des décisions, aperçu d'un profil, modification des fichiers YAML.
Aucune logique métier ici : tout passe par `application/regles_admin.py`."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from urllib.parse import quote

from humatheque.application import Introuvable, ReglesRefusees
from humatheque.regles.modeles import Noeud, Partie, SousZoneRegle, ZoneRegle
from humatheque.web.pages import COOKIE_AUTEUR, _app, _auteur, _rendre

routeur = APIRouter(include_in_schema=False)
ONGLETS = ("pipeline", "notice", "alertes", "essai", "modifier")


def _page(request: Request, onglet: str = "pipeline", **contexte):
    a = _app(request)
    onglet = onglet if onglet in ONGLETS else "pipeline"
    contexte.setdefault("fichier", a.vue_regles().fichiers[0])
    if onglet == "modifier" and "texte" not in contexte:
        contexte["texte"] = a.texte_regles(contexte["fichier"])
    if onglet == "notice":
        contexte["notice"] = a.vue_notice_modele(contexte.get("variante") or "socle")
    if onglet == "alertes":
        contexte["vue_alertes"] = a.vue_alertes()
    return _rendre(request, "admin_regles.html", vue=a.vue_regles(), onglet=onglet,
                   dossiers=a.dossiers_pour_essai(), **contexte)


@routeur.get("/admin/regles")
def page_regles(request: Request, onglet: str = "pipeline", fichier: str | None = None,
                profil: str | None = None, enregistre: str | None = None, variante: str = "socle",
                zone: str | None = None, dossier: str | None = None):
    contexte = {"profil": profil, "enregistre": enregistre, "variante": variante, "zone_ouverte": zone,
                "dossier_essai": dossier}
    if fichier:
        contexte["fichier"] = fichier
    return _page(request, onglet, **contexte)


# ---------------------------------------------------------------- pipeline (arbre par étape)
def _noeud_depuis_formulaire(f, possibles: dict[str, list[str]]) -> Noeud:
    """Champs du formulaire d'une branche → Noeud. Pour chaque variable : valeurs cochées et
    mode (« si » : une des valeurs ; « sauf » : aucune des valeurs)."""
    si, sauf = {}, {}
    for var in possibles:
        vals = [str(v) for v in f.getlist(f"v.{var}")]
        if not vals:
            continue
        val = vals[0] if len(vals) == 1 else vals
        (sauf if str(f.get(f"mode.{var}") or "si") == "sauf" else si)[var] = val
    sorte = str(f.get("resultat") or "")
    d = {"si": si, "sauf": sauf, "motif": str(f.get("motif") or "").strip() or None}
    if sorte and sorte != "branches":
        d |= {"resultat": sorte, "notice": str(f.get("notice") or "") or None,
              "alerte": str(f.get("alerte") or "").strip() or None}
        if sorte not in ("conversion", "création"):
            d["notice"] = None
    return Noeud.model_validate(d)


@routeur.get("/admin/regles/noeud")
def formulaire_noeud(request: Request, etape: str, chemin: str = "", operation: str = "modifier"):
    """Formulaire d'une branche : modifier, ajouter après, ajouter une sous-branche."""
    a = _app(request)
    from humatheque.regles.moteur import valeurs_possibles

    n = a.noeud(etape, chemin) if operation == "modifier" else None
    return _rendre(request, "_noeud_formulaire.html", etape=a.regles.etapes[etape], chemin=chemin,
                   operation=operation, noeud=n or Noeud(resultat="poursuite"),
                   possibles=valeurs_possibles(a.regles, etape), vue=a.vue_regles(),
                   embranchement=bool(n and n.branches), sinon=chemin.endswith("s"))


@routeur.post("/admin/regles/noeud")
async def enregistrer_noeud(request: Request):
    from humatheque.regles.moteur import valeurs_possibles

    a = _app(request)
    f = await request.form()
    etape, chemin = str(f.get("etape") or ""), str(f.get("chemin") or "")
    operation = str(f.get("operation") or "modifier")
    auteur = _auteur(str(f.get("auteur") or ""))
    noeud = _noeud_depuis_formulaire(f, valeurs_possibles(a.regles, etape))
    try:
        r = a.modifier_arbre(etape, chemin, operation, auteur, noeud)
    except ReglesRefusees as e:
        reponse = _page(request, "pipeline", refus=e.rapport)
        reponse.status_code = 422
        return reponse
    return _apres_enregistrement(f"/admin/regles?onglet=pipeline&enregistre={quote(r.archive)}#etape-{etape}", auteur)


@routeur.post("/admin/regles/noeud/operation")
def operation_noeud(request: Request, etape: Annotated[str, Form()], chemin: Annotated[str, Form()],
                    operation: Annotated[str, Form()], auteur: Annotated[str, Form()] = ""):
    a = _app(request)
    auteur = _auteur(auteur)
    try:
        r = a.modifier_arbre(etape, chemin, operation, auteur)
    except ReglesRefusees as e:
        reponse = _page(request, "pipeline", refus=e.rapport)
        reponse.status_code = 422
        return reponse
    return _apres_enregistrement(f"/admin/regles?onglet=pipeline&enregistre={quote(r.archive)}#etape-{etape}", auteur)


@routeur.post("/admin/regles/alertes")
async def enregistrer_alertes(request: Request):
    a = _app(request)
    f = await request.form()
    auteur = _auteur(str(f.get("auteur") or ""))
    familles = {code: {"libelle": str(f.get(f"libelle.{code}") or ""), "afficher": f.get(f"afficher.{code}") == "1"}
                for code in a.regles.alertes}
    try:
        r = a.enregistrer_alertes(familles, auteur)
    except ReglesRefusees as e:
        reponse = _page(request, "alertes", refus=e.rapport)
        reponse.status_code = 422
        return reponse
    return _apres_enregistrement(f"/admin/regles?onglet=alertes&enregistre={quote(r.archive)}", auteur)


@routeur.post("/admin/regles/essai")
async def essai(request: Request):
    """Banc d'essai : dossier (codes repris) ou exemple + valeurs saisies → parcours, données, notice."""
    a = _app(request)
    f = await request.form()
    dossier = str(f.get("dossier") or "")
    valeurs = {k: str(v) for k, v in f.items() if k not in ("dossier", "profil")}
    banc = a.banc_essai(dossier or None, valeurs, str(f.get("profil") or "") or None)
    return _rendre(request, "_banc_resultat.html", banc=banc)


@routeur.post("/admin/regles/non-regression")
def non_regression(request: Request):
    return _rendre(request, "_non_regression.html", nr=_app(request).non_regression())


@routeur.post("/admin/regles/apercu")
def apercu_profil(request: Request, profil: Annotated[str, Form()], dossier: Annotated[str, Form()] = ""):
    return _rendre(request, "_regles_apercu.html", apercu=_app(request).apercu_profil(profil, dossier or None))


@routeur.post("/admin/regles/verifier")
def verifier_brouillon(request: Request, fichier: Annotated[str, Form()], texte: Annotated[str, Form()]):
    return _rendre(request, "_regles_rapport.html", rapport=_app(request).verifier_brouillon(fichier, texte),
                   brouillon=True)


@routeur.post("/admin/regles/enregistrer")
def enregistrer(request: Request, fichier: Annotated[str, Form()], texte: Annotated[str, Form()],
                auteur: Annotated[str, Form()] = ""):
    a = _app(request)
    try:
        r = a.enregistrer_regles(fichier, texte, _auteur(auteur))
    except ReglesRefusees as e:
        reponse = _page(request, "modifier", fichier=fichier, texte=texte, refus=e.rapport)
        reponse.status_code = 422
        return reponse
    reponse = RedirectResponse(f"/admin/regles?onglet=modifier&fichier={fichier}&enregistre={r.archive}",
                               status_code=303)
    reponse.set_cookie(COOKIE_AUTEUR, auteur.strip(), max_age=3600 * 24 * 365, samesite="lax")
    return reponse


# ---------------------------------------------------------------- notice UNIMARC (zones structurées)
def _zone_depuis_formulaire(f) -> ZoneRegle:
    """Champs du formulaire de zone → ZoneRegle (brouillon toléré : rien n'est écrit ici)."""
    sous_zones = []
    indices = sorted({int(k.split("-")[1]) for k in f.keys() if k.startswith("sz-") and k.split("-")[1].isdigit()})
    for i in indices:
        p = f"sz-{i}-"
        g = str(f.get(p + "genre") or "constante")
        d: dict = {"code": str(f.get(p + "code") or ""), "libelle": str(f.get(p + "libelle") or ""),
                   "remarque": str(f.get(p + "remarque") or "") or None}
        if g == "constante":
            d["constante"] = str(f.get(p + "constante") or "")
        elif g == "variable":
            d |= {"variable": str(f.get(p + "variable") or "") or None, "attribut": str(f.get(p + "attribut") or "") or None,
                  "prefixe": str(f.get(p + "prefixe") or ""), "suffixe": str(f.get(p + "suffixe") or ""),
                  "liste": str(f.get(p + "liste") or "repeter"), "separateur": str(f.get(p + "separateur") or ", "),
                  "dernier": str(f.get(p + "dernier")) if f.get(p + "dernier") not in (None, "") else None}
        elif g == "composition":
            js = sorted({int(k.split("-")[3]) for k in f.keys() if k.startswith(p + "p-")})
            d["parties"] = [{"variable": str(f.get(f"{p}p-{j}-variable") or "") or None,
                             "texte": None if f.get(f"{p}p-{j}-variable") else str(f.get(f"{p}p-{j}-texte") or "")}
                            for j in js] or [{"texte": ""}]
        elif g == "autorite":
            d |= {"autorite": str(f.get(p + "autorite") or "") or None, "rendu": str(f.get(p + "rendu") or "") or None}
        if g != "constante":
            d["si_absente"] = str(f.get(p + "si_absente") or "omettre")
            d["repli"] = str(f.get(p + "repli")) if f.get(p + "repli") not in (None, "") else None
            if g == "variable" and not d.get("variable"):
                d["variable"] = "titre"  # première sélection : une donnée par défaut, modifiable
            if g == "autorite" and not d.get("autorite"):
                d["autorite"] = "auteur"
        sous_zones.append({k: v for k, v in d.items() if v is not None})
    priorite = str(f.get("priorite") or "")
    return ZoneRegle.model_validate({
        "id": str(f.get("id") or "").strip(), "zone": str(f.get("zone") or "").strip(),
        "libelle": str(f.get("libelle") or ""), "indicateurs": str(f.get("indicateurs") or "") or None,
        "sous_zones": sous_zones, "priorite": int(priorite) if priorite.isdigit() else None,
        "à_valider": bool(f.get("a_valider")), "actif": bool(f.get("actif")),
        "remarque": str(f.get("remarque") or "") or None})


def _appliquer_action(z: ZoneRegle, action: str) -> tuple[ZoneRegle, int | None]:
    """Action de brouillon sur les sous-zones ; renvoie la zone et la sous-zone à ouvrir."""
    szs = list(z.sous_zones)
    nom, _, args = action.partition(":")
    a = [int(x) for x in args.split(":") if x.isdigit()]
    ouvrir = a[0] if a else None
    if nom == "ajouter_sz":
        szs.append(SousZoneRegle(code="a", constante=""))
        ouvrir = len(szs) - 1
    elif nom == "supprimer_sz" and a and a[0] < len(szs):
        szs.pop(a[0])
        ouvrir = None
    elif nom in ("monter_sz", "descendre_sz") and a:
        j = a[0] + (-1 if nom == "monter_sz" else 1)
        if 0 <= j < len(szs):
            szs[a[0]], szs[j] = szs[j], szs[a[0]]
            ouvrir = j
    elif nom == "ajouter_partie" and a and a[0] < len(szs):
        szs[a[0]] = szs[a[0]].model_copy(update={"parties": [*(szs[a[0]].parties or []), Partie(texte="")]})
    elif nom == "supprimer_partie" and len(a) == 2 and a[0] < len(szs):
        parties = list(szs[a[0]].parties or [])
        if a[1] < len(parties):
            parties.pop(a[1])
        szs[a[0]] = szs[a[0]].model_copy(update={"parties": parties})
    return z.model_copy(update={"sous_zones": szs}), ouvrir


def _contexte_editeur(request: Request, variante: str, zone: ZoneRegle, ancien_id: str | None,
                      origine: str | None, refus_zone=None) -> dict:
    from humatheque.regles.moteur import conventions

    a = _app(request)
    lignes, alertes = a.apercu_zone(zone, variante)
    vue = a.vue_notice_modele(variante)
    return dict(zone=zone, variante=variante, ancien_id=ancien_id, origine=origine, refus_zone=refus_zone,
                vue_variables=a.variables_studio(), attributs=conventions()["attributs"], modes_rendu=vue.modes_rendu,
                rendus=vue.rendus, apercu_zone=lignes, apercu_alertes=alertes)


def _editeur_zone(request: Request, variante: str, zone: ZoneRegle, ancien_id: str | None, origine: str | None,
                  ouvrir: int | None = None, retiree: bool = False):
    return _rendre(request, "_notice_zone.html", retiree=retiree, ouvrir=ouvrir,
                   **_contexte_editeur(request, variante, zone, ancien_id, origine))


@routeur.get("/admin/regles/notice/zone")
def editeur_zone(request: Request, variante: str = "socle", id: str = ""):
    a = _app(request)
    if not id:
        vide = ZoneRegle(id="", zone="", sous_zones=[SousZoneRegle(code="a", constante="")])
        return _editeur_zone(request, variante, vide, None, None, ouvrir=0)
    zv = next((zv for zv in a.vue_notice_modele(variante).zones if zv.zone.id == id), None)
    if zv is None:
        raise Introuvable(f"zone {id}")
    return _editeur_zone(request, variante, zv.zone, id, zv.origine, retiree=zv.retiree)


@routeur.post("/admin/regles/notice/zone/brouillon")
async def brouillon_zone(request: Request):
    f = await request.form()
    zone, ouvrir = _appliquer_action(_zone_depuis_formulaire(f), str(f.get("action") or ""))
    if ouvrir is None and str(f.get("ouvrir") or "").isdigit():
        ouvrir = int(str(f.get("ouvrir")))
    return _editeur_zone(request, str(f.get("variante") or "socle"), zone, str(f.get("ancien_id") or "") or None,
                         str(f.get("origine") or "") or None, ouvrir)


@routeur.post("/admin/regles/notice/zone")
async def enregistrer_zone(request: Request):
    a = _app(request)
    f = await request.form()
    variante = str(f.get("variante") or "socle")
    auteur = _auteur(str(f.get("auteur") or ""))
    zone = _zone_depuis_formulaire(f)
    ancien = str(f.get("ancien_id") or "") or None
    try:
        r = a.enregistrer_zone(variante, ancien, zone, str(f.get("portee") or "socle"), auteur)
    except ReglesRefusees as e:
        reponse = _page(request, "notice", variante=variante,
                        editeur=_contexte_editeur(request, variante, zone, ancien, str(f.get("origine") or "") or None,
                                                  e.rapport))
        reponse.status_code = 422
        return reponse
    return _apres_enregistrement(f"/admin/regles?onglet=notice&variante={quote(variante)}&zone={quote(zone.id)}"
                                 f"&enregistre={quote(r.archive)}", auteur)


@routeur.post("/admin/regles/notice/zone/operation")
def operation_zone(request: Request, variante: Annotated[str, Form()], id: Annotated[str, Form()],
                   operation: Annotated[str, Form()], auteur: Annotated[str, Form()] = ""):
    a = _app(request)
    auteur = _auteur(auteur)
    try:
        r = a.modifier_presence_zone(variante, id, operation, auteur)
    except ReglesRefusees as e:
        reponse = _page(request, "notice", variante=variante, refus=e.rapport)
        reponse.status_code = 422
        return reponse
    return _apres_enregistrement(f"/admin/regles?onglet=notice&variante={quote(variante)}"
                                 f"&enregistre={quote(r.archive)}", auteur)


@routeur.post("/admin/regles/notice/rendus")
async def enregistrer_rendus(request: Request):
    a = _app(request)
    f = await request.form()
    auteur = _auteur(str(f.get("auteur") or ""))
    rendus = {k.removeprefix("rendu."): str(v) for k, v in f.items() if k.startswith("rendu.")}
    try:
        r = a.enregistrer_rendus(rendus, auteur)
    except ReglesRefusees as e:
        reponse = _page(request, "notice", refus=e.rapport)
        reponse.status_code = 422
        return reponse
    return _apres_enregistrement(f"/admin/regles?onglet=notice&enregistre={quote(r.archive)}", auteur)


def _apres_enregistrement(url: str, auteur: str):
    reponse = RedirectResponse(url, status_code=303)
    reponse.set_cookie(COOKIE_AUTEUR, auteur.strip(), max_age=3600 * 24 * 365, samesite="lax")
    return reponse
