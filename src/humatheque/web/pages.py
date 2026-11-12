"""Pages HTML (SPEC §9.3) et fragments HTMX. Chaque route appelle une fonction
de `application/` et rend un gabarit ; aucune logique métier ici."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from markupsafe import escape

from humatheque.application import ActionImpossible, Application, Introuvable, documents_depuis_liste
from humatheque.application.vues import EntreeLot
from humatheque.web import gabarits

routeur = APIRouter(include_in_schema=False)
COOKIE_AUTEUR = "catalogueur"


def _app(request: Request) -> Application:
    return request.app.state.application


def _rendre(request: Request, gabarit: str, **contexte):
    contexte.setdefault("auteur", request.cookies.get(COOKIE_AUTEUR, ""))
    return gabarits.TemplateResponse(request, gabarit, contexte)


def _rediriger(url: str, auteur: str | None = None) -> RedirectResponse:
    r = RedirectResponse(url, status_code=303)
    if auteur:
        r.set_cookie(COOKIE_AUTEUR, auteur, max_age=3600 * 24 * 365, samesite="lax")
    return r


# ---------------------------------------------------------------- accueil
COOKIE_VISITE = "derniere_visite"


@routeur.get("/")
def accueil(request: Request):
    from datetime import UTC, datetime

    a = _app(request)
    try:
        visite = datetime.fromisoformat(request.cookies.get(COOKIE_VISITE, ""))
    except ValueError:
        visite = None
    nouveaux = a.lots_automatiques_depuis(visite) if visite else []
    r = _rendre(request, "accueil.html", vue=a.accueil(), nouveaux_lots=nouveaux, synthese=a.synthese_accueil())
    r.set_cookie(COOKIE_VISITE, datetime.now(UTC).isoformat(), max_age=3600 * 24 * 365, samesite="lax")
    return r


@routeur.get("/traiter")
def traiter(request: Request):
    """Chargement manuel : téléversement, URL, choix du fournisseur et paramètres avancés."""
    return _rendre(request, "traiter.html", vue=_app(request).accueil())


@routeur.post("/spec/recharger")
def recharger_spec(request: Request, retour: Annotated[str, Form()] = "/"):
    etat = _app(request).recharger_spec()
    if request.headers.get("HX-Request"):
        return _rendre(request, "_spec.html", spec=etat)
    return _rediriger(retour if retour.startswith("/") and not retour.startswith("//") else "/")


@routeur.post("/lots")
async def creer_lot(
    request: Request,
    type_document: Annotated[str, Form()] = "these",
    fournisseur: Annotated[str, Form()] = "",
    url: Annotated[str, Form()] = "",
    liste_urls: Annotated[str, Form()] = "",
    un_seul_document: Annotated[bool, Form()] = False,
    images: Annotated[list[UploadFile] | None, File()] = None,
    dossiers: Annotated[list[UploadFile] | None, File()] = None,
):
    a = _app(request)
    # Téléversement d'une arborescence (input webkitdirectory : nom = chemin relatif)
    arborescence = [(f.filename or "", await f.read()) for f in (dossiers or []) if f.filename]
    if arborescence:
        if type_document not in ("these", "memoire"):
            raise ActionImpossible(f"type de document inconnu : {type_document}")
        lot = a.televerser_dossiers(arborescence, type_document, fournisseur or None,
                                    await _reglages_formulaire(request))
        return _rediriger(f"/lots/{lot.id}")
    fichiers = [(f.filename or "image", await f.read()) for f in (images or []) if f.filename]
    if fichiers:
        documents, source = a.enregistrer_televersement(fichiers, un_seul_document), "televersement"
    elif url.strip():
        documents, source = [[url.strip()]], "url"
    elif liste_urls.strip():
        documents, source = documents_depuis_liste(liste_urls), "liste"
    else:
        raise ActionImpossible("Indiquez des images : téléversement, URL ou liste d'URL.")
    if type_document not in ("these", "memoire"):
        raise ActionImpossible(f"type de document inconnu : {type_document}")
    lot = a.creer_lot(EntreeLot(type_document=type_document, fournisseur=fournisseur or None,
                                documents=documents, source=source,
                                reglages_services=await _reglages_formulaire(request)))
    if lot.mode == 1:
        (ligne,) = a.store.lignes_du_lot(lot.id)
        return _rediriger(f"/dossiers/{ligne.id}")
    return _rediriger(f"/lots/{lot.id}")


# ---------------------------------------------------------------- lots
@routeur.get("/lots")
def lots(request: Request, etat: str = "", declencheur: str = "", type_document: str = ""):
    return _rendre(request, "lots.html", vue=_app(request).vue_lots(etat, declencheur, type_document))


# ---------------------------------------------------------------- lot (mode 2)
@routeur.get("/lots/{lot_id}")
def lot(request: Request, lot_id: str, filtre: str = "tous"):
    return _rendre(request, "lot.html", vue=_app(request).vue_lot(lot_id, filtre))


@routeur.get("/lots/{lot_id}/fragment")
def lot_fragment(request: Request, lot_id: str, filtre: str = "tous"):
    """File d'exceptions + journal, indicateurs et bandeau en hx-swap-oob."""
    return _rendre(request, "_lot_fragment.html", vue=_app(request).vue_lot(lot_id, filtre), oob=True)


@routeur.get("/lots/{lot_id}/rapport.csv")
def rapport(request: Request, lot_id: str):
    return Response(_app(request).export_csv(lot_id), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="rapport-{lot_id}.csv"'})


@routeur.get("/lots/{lot_id}/notices.{format_}")
def notices(request: Request, lot_id: str, format_: str):
    if format_ not in ("mrc", "xml"):
        raise Introuvable(f"format {format_}")
    mime = "application/marc" if format_ == "mrc" else "application/xml"
    return Response(_app(request).export_notices(lot_id, format_), media_type=mime,
                    headers={"Content-Disposition": f'attachment; filename="notices-{lot_id}.{format_}"'})


# ---------------------------------------------------------------- dossier (mode 1)
@routeur.get("/dossiers/{dossier_id}")
def dossier(request: Request, dossier_id: str):
    a = _app(request)
    return _rendre(request, "dossier.html", vue=a.vue_dossier(dossier_id), notice=a.vue_notice(dossier_id))


@routeur.get("/dossiers/{dossier_id}/apercu")
def apercu(request: Request, dossier_id: str):
    """Consultation en lecture seule : images et notice finale."""
    return _rendre(request, "apercu.html", vue=_app(request).vue_apercu(dossier_id))


@routeur.get("/dossiers/{dossier_id}/fragment")
def dossier_fragment(request: Request, dossier_id: str):
    """Panneau droit du dossier, rafraîchi tant que le traitement est en cours ; à la fin,
    rechargement complet pour monter l'éditeur (îlot hors fragment rafraîchi)."""
    vue = _app(request).vue_dossier(dossier_id)
    reponse = _rendre(request, "_dossier_panneau.html", vue=vue, oob=True)
    if vue.etat == "termine":
        reponse.headers["HX-Refresh"] = "true"
    return reponse


@routeur.get("/dossiers/{dossier_id}/versions")
def versions_notice(request: Request, dossier_id: str):
    """Versions de la notice et régénération en attente (rafraîchi après chaque enregistrement)."""
    return _rendre(request, "_notice_versions.html", notice=_app(request).vue_notice(dossier_id),
                   dossier_id=dossier_id)


@routeur.post("/dossiers/{dossier_id}/propositions/{numero}")
def proposition(request: Request, dossier_id: str, numero: int, choix: Annotated[str, Form()],
                auteur: Annotated[str, Form()]):
    a = _app(request)
    if choix == "reporter":
        a.reporter_proposition(dossier_id, numero, _auteur(auteur))
    else:
        a.ignorer_proposition(dossier_id, numero, _auteur(auteur))
    return _rediriger(f"/dossiers/{dossier_id}", auteur)


@routeur.get("/dossiers/{dossier_id}/telecharger/{fichier}")
def telecharger(request: Request, dossier_id: str, fichier: str):
    nom, contenu, mime = _app(request).telecharger(dossier_id, fichier)
    return Response(contenu, media_type=mime, headers={"Content-Disposition": f'attachment; filename="{nom}"'})


@routeur.post("/dossiers/{dossier_id}/relancer")
def relancer(request: Request, dossier_id: str, depuis: Annotated[str, Form()] = ""):
    _app(request).relancer(dossier_id, depuis or None)
    return _rediriger(f"/dossiers/{dossier_id}")


@routeur.post("/dossiers/{dossier_id}/provenance")
def provenance(request: Request, dossier_id: str, valeur: Annotated[str, Form()],
               auteur: Annotated[str, Form()], commentaire: Annotated[str, Form()] = ""):
    _app(request).choisir_provenance(dossier_id, valeur, _auteur(auteur), commentaire)
    return _rediriger(f"/dossiers/{dossier_id}", auteur)


@routeur.post("/dossiers/{dossier_id}/faux-positif")
def faux_positif(request: Request, dossier_id: str, service: Annotated[str, Form()],
                 auteur: Annotated[str, Form()], commentaire: Annotated[str, Form()] = ""):
    _app(request).declarer_faux_positif(dossier_id, service, commentaire, _auteur(auteur))
    return _rediriger(f"/dossiers/{dossier_id}", auteur)


@routeur.post("/dossiers/{dossier_id}/ppn")
def ppn(request: Request, dossier_id: str, exception: Annotated[int, Form()], auteur: Annotated[str, Form()],
        ppn: Annotated[str, Form()] = "", saisie: Annotated[str, Form()] = ""):
    choix = (saisie or ppn).strip()
    _app(request).choisir_ppn(dossier_id, exception, None if choix in ("", "sans-lien") else choix, _auteur(auteur))
    return _rediriger(f"/dossiers/{dossier_id}", auteur)


@routeur.post("/dossiers/{dossier_id}/ppns")
async def ppns(request: Request, dossier_id: str):
    """Validation groupée des choix IdRef : champs ppn_<index> (candidat ou « sans-lien »)
    et saisie_<index> (PPN saisi, prioritaire) ; une exception sans choix reste ouverte."""
    formulaire = await request.form()
    auteur = str(formulaire.get("auteur", ""))
    choix: dict[int, str | None] = {}
    for cle in formulaire:
        prefixe, _, index = cle.partition("_")
        if prefixe not in ("ppn", "saisie") or not index.isdigit():
            continue
        i = int(index)
        valeur = (str(formulaire.get(f"saisie_{i}", "")).strip() or str(formulaire.get(f"ppn_{i}", "")).strip())
        if valeur:
            choix[i] = None if valeur == "sans-lien" else valeur
    _app(request).choisir_ppns(dossier_id, choix, _auteur(auteur))
    return _rediriger(f"/dossiers/{dossier_id}", auteur)


@routeur.post("/dossiers/{dossier_id}/dewey")
def dewey(request: Request, dossier_id: str, exception: Annotated[int, Form()], code: Annotated[str, Form()],
          auteur: Annotated[str, Form()]):
    _app(request).choisir_dewey(dossier_id, exception, code, _auteur(auteur))
    return _rediriger(f"/dossiers/{dossier_id}", auteur)


@routeur.post("/dossiers/{dossier_id}/notice-existante")
def notice_existante(request: Request, dossier_id: str, auteur: Annotated[str, Form()],
                     commentaire: Annotated[str, Form()] = ""):
    _app(request).confirmer_notice_existante(dossier_id, commentaire, _auteur(auteur))
    return _rediriger(f"/dossiers/{dossier_id}", auteur)


@routeur.post("/dossiers/{dossier_id}/hors-outil")
def hors_outil(request: Request, dossier_id: str, auteur: Annotated[str, Form()],
               commentaire: Annotated[str, Form()] = ""):
    _app(request).traiter_hors_outil(dossier_id, commentaire, _auteur(auteur))
    return _rediriger(f"/dossiers/{dossier_id}", auteur)


async def _reglages_formulaire(request: Request) -> dict:
    """Champs « reglage.<service>.<paramètre> » du formulaire → {service: {paramètre: valeur}}."""
    reglages: dict = {}
    for cle, valeur in (await request.form()).multi_items():
        if cle.startswith("reglage.") and cle.count(".") == 2 and isinstance(valeur, str):
            _, service, parametre = cle.split(".")
            reglages.setdefault(service, {})[parametre] = valeur
    return reglages


def _auteur(auteur: str) -> str:
    if not auteur.strip():
        raise ActionImpossible("Indiquez votre nom (catalogueur) pour journaliser la résolution.")
    return auteur.strip()


# ---------------------------------------------------------------- historique, fichiers
@routeur.get("/historique")
def historique(request: Request):
    return _rendre(request, "historique.html", vue=_app(request).historique())


@routeur.get("/fichiers/{chemin:path}")
def fichier(request: Request, chemin: str):
    return FileResponse(_app(request).chemin_fichier(chemin))


# ---------------------------------------------------------------- stockage S3 (SPEC §3.1)
@routeur.post("/minio/balayer")
def balayer(request: Request):
    """Liste les dossiers du bucket et leur dernier statut (balayage journalisé)."""
    a = _app(request)
    return _rendre(request, "_minio.html", vue=a.balayer("manuel"), fournisseurs=a.accueil().fournisseurs,
                   reglables=a.reglables())


@routeur.post("/minio/lancer")
async def lancer_selection(request: Request, dossiers: Annotated[list[str] | None, Form()] = None,
                           fournisseur: Annotated[str, Form()] = ""):
    lot = _app(request).lancer_selection(dossiers or [], fournisseur or None,
                                         reglages=await _reglages_formulaire(request))
    if request.headers.get("HX-Request"):
        r = Response(status_code=204)
        r.headers["HX-Redirect"] = f"/lots/{lot.id}"
        return r
    return _rediriger(f"/lots/{lot.id}")


@routeur.get("/minio/objet")
def objet_s3(request: Request, uri: str):
    contenu, mime = _app(request).objet_s3(uri)
    return Response(contenu, media_type=mime, headers={"Cache-Control": "private, max-age=3600"})


@routeur.post("/minio/evenements")
def evenements_s3(request: Request, jeton: str = ""):
    """Notification d'évènement S3 (ING-F-003) : signalée au planificateur, qui crée le lot
    après le délai de stabilisation. Protégée par le jeton affiché dans /admin."""
    import hmac

    a = _app(request)
    ev = a.reglages_admin().declencheurs.evenements
    if not ev.actif:
        return Response("Déclencheur « évènements S3 » désactivé (page Administration).", status_code=403)
    fourni = jeton or request.headers.get("Authorization", "").removeprefix("Bearer ")
    if not ev.jeton or not hmac.compare_digest(fourni, ev.jeton):
        return Response("Jeton absent ou invalide.", status_code=401)
    request.app.state.planificateur.signaler_evenement()
    return Response(f"Évènement pris en compte : traitement après {ev.delai_secondes} s sans nouvel évènement.",
                    status_code=202)


# ---------------------------------------------------------------- administration (étape 8)
@routeur.get("/admin")
def administration(request: Request):
    a = _app(request)
    return _rendre(request, "admin.html", reglages=a.reglages_admin(), reglables=a.reglables(),
                   fournisseurs=a.accueil().fournisseurs, vlm=a.fournisseurs_vlm(), etat=request.app.state.planificateur.etat(),
                   s3=a.depot is not None, base=str(request.base_url).rstrip("/"),
                   mot_de_passe=bool(a.reglages.mot_de_passe.get_secret_value()))


@routeur.get("/admin/prompts")
def page_prompts(request: Request, nom: str = "extraction_these.txt", enregistre: str = "", variables: str = ""):
    a = _app(request)
    return _rendre(request, "admin_prompts.html", prompts=a.prompts(), prompt=a.prompt(nom),
                   correspondances=a.correspondances(), journal=a.journal_prompts(), enregistre=enregistre,
                   variables_enregistrees=bool(variables))


@routeur.post("/admin/prompts")
def enregistrer_prompt(request: Request, nom: Annotated[str, Form()], texte: Annotated[str, Form()],
                       auteur: Annotated[str, Form()] = ""):
    a = _app(request)
    try:
        a.enregistrer_prompt(nom, texte, _auteur(auteur))
    except ActionImpossible as e:  # brouillon conservé, motif affiché
        r = _rendre(request, "admin_prompts.html", prompts=a.prompts(), prompt=a.prompt(nom), texte=texte,
                    refus=str(e), correspondances=a.correspondances(), journal=a.journal_prompts(), enregistre="")
        r.status_code = 422
        return r
    return _rediriger(f"/admin/prompts?nom={nom}&enregistre=1", auteur)


@routeur.post("/admin/prompts/variables")
async def enregistrer_variables(request: Request):
    from humatheque.application import ReglesRefusees

    a = _app(request)
    f = await request.form()
    nom = str(f.get("nom") or "extraction_these.txt")
    auteur = _auteur(str(f.get("auteur") or ""))
    indices = sorted({int(k.split("-")[1]) for k in f.keys() if k.startswith("l-") and k.split("-")[1].isdigit()})
    lignes = [{"champ": f.get(f"l-{i}-champ"), "ancien": f.get(f"l-{i}-ancien"), "variable": f.get(f"l-{i}-variable"),
               "type": f.get(f"l-{i}-type"), "liste": f.get(f"l-{i}-liste") == "1",
               "disponible": f.get(f"l-{i}-disponible") == "1"} for i in indices]
    try:
        a.enregistrer_variables(lignes, auteur)
    except (ReglesRefusees, ActionImpossible) as e:
        r = _rendre(request, "admin_prompts.html", prompts=a.prompts(), prompt=a.prompt(nom),
                    correspondances=a.correspondances(), journal=a.journal_prompts(), enregistre="",
                    refus_variables=getattr(e, "rapport", None) or str(e))
        r.status_code = 422
        return r
    return _rediriger(f"/admin/prompts?nom={nom}&variables=1", auteur)


@routeur.get("/admin/modeles")
def modeles(request: Request, fournisseur: str):
    """Options de la liste des modèles d'un fournisseur (chargées à l'ouverture de la liste)."""
    a = _app(request)
    vue = next((v for v in a.fournisseurs_vlm() if v.nom == fournisseur), None)
    if vue is None:
        raise Introuvable(f"fournisseur {fournisseur}")
    deja = {vue.modele_env, vue.modele}
    noms = [m for m in a.modeles_accessibles(fournisseur) if m not in deja]
    if not noms:
        return HTMLResponse('<option disabled>(aucun autre modèle obtenu de l\'API)</option>')
    return HTMLResponse("".join(f'<option value="{escape(m)}">{escape(m)}</option>' for m in noms))


@routeur.get("/admin/endpoint-hf")
def endpoint_hf_etat(request: Request, fournisseur: str = ""):
    """État de l'endpoint HF dédié (fragment chargé à l'affichage, sans le réveiller) ;
    `fournisseur` (formulaire de traitement) : rien si un autre fournisseur est choisi."""
    if fournisseur and fournisseur != "custom-hf-endpoint":
        return HTMLResponse("")
    return gabarits.TemplateResponse(request, "_endpoint_hf.html", {"e": _app(request).etat_endpoint_hf()})


@routeur.post("/admin/endpoint-hf/reveiller")
def endpoint_hf_reveiller(request: Request):
    """Réveil explicite de l'endpoint HF dédié (resume ou requête de santé)."""
    return gabarits.TemplateResponse(request, "_endpoint_hf.html",
                                     {"e": _app(request).etat_endpoint_hf(reveiller=True), "reveil": True})


@routeur.get("/admin/sante")
def sante(request: Request):
    return _rendre(request, "_sante.html", sante=_app(request).sante())


@routeur.post("/admin/reglages")
async def reglages_admin(request: Request):
    from humatheque.application.administration import (
        Balayage, Declencheurs, Evenements, Planification, ReglagesAdmin,
    )

    a = _app(request)
    f = await request.form()
    actuel = a.reglages_admin()

    def entier(cle: str, defaut: int) -> int:
        try:
            return int(str(f.get(cle) or defaut))
        except ValueError as e:
            raise ActionImpossible(f"{cle} : nombre entier attendu") from e

    try:
        nouveaux = ReglagesAdmin(
            fournisseur=str(f.get("fournisseur") or actuel.fournisseur),
            modeles={k.removeprefix("modele."): str(v).strip() for k, v in f.items()
                     if k.startswith("modele.") and str(v).strip()},
            reglages_services=await _reglages_formulaire(request),
            declencheurs=Declencheurs(
                balayage=Balayage(actif=f.get("balayage_actif") == "on",
                                  intervalle_minutes=entier("balayage_intervalle", 60)),
                planification=Planification(actif=f.get("planification_actif") == "on",
                                            cron=str(f.get("planification_cron") or "").strip()),
                evenements=Evenements(actif=f.get("evenements_actif") == "on",
                                      jeton=actuel.declencheurs.evenements.jeton,
                                      delai_secondes=entier("evenements_delai", 60))))
    except ValueError as e:  # bornes pydantic
        raise ActionImpossible(f"réglage invalide : {e.errors()[0]['loc'][-1]} hors bornes"
                               if hasattr(e, "errors") else str(e)) from e
    a.modifier_reglages_admin(nouveaux, _auteur(str(f.get("auteur") or "")))
    return _rediriger("/admin", str(f.get("auteur") or ""))


@routeur.post("/admin/jeton")
def jeton(request: Request, auteur: Annotated[str, Form()]):
    _app(request).renouveler_jeton(_auteur(auteur))
    return _rediriger("/admin", auteur)


@routeur.post("/admin/tick")
def tick(request: Request):
    """Exécute un tour du planificateur tout de suite (essai)."""
    resultat = request.app.state.planificateur.tick()
    return _rediriger("/admin")


# ---------------------------------------------------------------- connexion (MOT_DE_PASSE)
@routeur.get("/login")
def connexion(request: Request, suite: str = "/"):
    return _rendre(request, "login.html", suite=suite, erreur=None)


@routeur.post("/login")
def connexion_post(request: Request, mot_de_passe: Annotated[str, Form()], suite: Annotated[str, Form()] = "/"):
    import hmac

    from humatheque.web.auth import COOKIE, jeton_session

    attendu = _app(request).reglages.mot_de_passe.get_secret_value()
    if not attendu:
        return _rediriger("/")
    if not hmac.compare_digest(mot_de_passe, attendu):
        r = _rendre(request, "login.html", suite=suite, erreur="Mot de passe incorrect.")
        r.status_code = 401
        return r
    r = _rediriger(suite if suite.startswith("/") and not suite.startswith("//") else "/")
    r.set_cookie(COOKIE, jeton_session(attendu), httponly=True, samesite="lax", max_age=3600 * 12)
    return r


@routeur.get("/logout")
def deconnexion(request: Request):
    from humatheque.web.auth import COOKIE

    r = _rediriger("/login")
    r.delete_cookie(COOKIE)
    return r
