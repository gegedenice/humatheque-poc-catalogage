"""Orchestrateur : un document → un Dossier (SPEC §6).

Chaque étape écrit `{statut, requete, reponse, duree, erreur}` dans le
dossier. `executer` repart de la première étape non terminée : les étapes
réussies ne sont pas rappelées (ING-F-007), sauf `depuis=<étape>`. Les
erreurs relançables (image inaccessible, inférence, services) sont retentées
`RELANCES_AUTO` fois avec un délai croissant, puis le dossier reste en incident.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

from humatheque import issues
from humatheque.config import charger_yaml, reglages
from humatheque.contexte import ResultatsServices, construire, document_vide, normaliser_metadonnees
from humatheque.conversion import ConversionEchec, convertir, depuis_iso, unimarc_xml_vers_iso, vers_winibw as iso_vers_winibw
from humatheque.decision.moteur import decider, faits_du_dossier, routage_final, routage_service
from humatheque.decision.provenance import provenance as deduire_provenance
from humatheque.extraction import (
    ExtractionInvalide, ImageIllisible, ImageInaccessible, InferenceIndisponible, extraire,
)
from humatheque.extraction.fournisseurs import FournisseurVLM
from humatheque.generateur import GenerationEchec, Generateur, vers_winibw
from humatheque.qualification import agreger
from humatheque.services import ClientService, ServiceIndisponible
from humatheque.services.alma import verifier_alma
from humatheque.services.dewey import classer
from humatheque.services.idref import aligner_organisations, aligner_personne
from humatheque.services.sudoc import lien_notice, lire_notice, verifier_sudoc
from humatheque.regles.modeles import Regles
from humatheque.workflow.modeles import ETAPES, Dossier, Etape, ExceptionDossier, maintenant

SERVICE_IDREF_PERSONNE = "idref_personne"   # clés de config/services.yaml
SERVICE_IDREF_ORGS = "idref_organisations"


@lru_cache
def _cfg() -> dict[str, Any]:
    return charger_yaml("decision")


@lru_cache
def _cfg_contexte() -> dict[str, Any]:
    return charger_yaml("contexte")


@dataclass
class Services:
    """Dépendances du workflow (injectées : réelles ou simulées)."""

    regles: Regles
    vlm: FournisseurVLM
    alma: ClientService
    sudoc: ClientService
    idref: ClientService
    dewey: ClientService
    seuil_dewey: float | None = None
    depot: Any = None  # ingestion.Depot : lit les images s3:// (None : stockage non configuré)
    # Réglages du lot (« Paramètres avancés ») : {service: {paramètre: valeur}}
    reglages_lot: dict[str, dict[str, Any]] = field(default_factory=dict)

    def parametres(self, service: str) -> dict[str, Any]:
        """Paramètres de requête : config/services.yaml, puis réglages du lot."""
        p = {**(charger_yaml("services")[service].get("parametres") or {}), **self.reglages_lot.get(service, {})}
        return p

    def lire_s3(self, u: str) -> bytes:
        from humatheque.ingestion import cle_depuis_uri

        return self.depot.lire(cle_depuis_uri(u, self.depot.bucket))


@dataclass
class Suite:
    """Résultat d'une étape : continuer, ou s'arrêter avec un routage et un motif."""

    continuer: bool = True
    routage: str = issues.TERMINE
    motif: str | None = None
    statut: str = "ok"


class _Incident(Exception):
    def __init__(self, statut: str, motif: str, appels: list | None = None, relancable: bool = False) -> None:
        super().__init__(motif)
        self.statut, self.motif, self.appels, self.relancable = statut, motif, appels or [], relancable


@dataclass
class Workflow:
    services: Services
    relances: int | None = None
    delai: float | None = None
    attendre: Callable[[float], None] = time.sleep
    _etapes: dict[str, Callable[[Dossier], Suite]] = field(init=False)
    _alignements_acquis: dict[str, dict] = field(init=False, default_factory=dict)

    def __post_init__(self) -> None:
        r = reglages()
        self.relances = r.relances_auto if self.relances is None else self.relances
        self.delai = r.relance_delai if self.delai is None else self.delai
        self._etapes = {n: getattr(self, f"_etape_{n}") for n in ETAPES}

    # ================================================================ exécution
    def executer(self, dossier: Dossier, depuis: str | None = None) -> Dossier:
        if depuis:
            dossier.reinitialiser_depuis(depuis)
            dossier.noter(depuis, "relance", f"relance demandée depuis {depuis}")
        dossier.version_spec = self.services.regles.version
        dossier.fournisseur_vlm = f"{self.services.vlm.nom}:{self.services.vlm.modele}"
        dossier.routage = dossier.motif_arret = dossier.etape_arret = None

        for nom in ETAPES:
            etape = dossier.etapes[nom]
            if (ETAPES.index(nom) > ETAPES.index("conversion") and dossier.decision
                    and dossier.decision.suite != "creer"):
                # Conversion faite : arrêt (SPEC §7), même lors d'une relance.
                conv = dossier.etapes["conversion"]
                dossier.etape_arret, dossier.motif_arret = "conversion", conv.motif
                break
            if etape.terminee:
                continue
            suite = self._lancer(dossier, nom)
            if not suite.continuer:
                dossier.etape_arret, dossier.motif_arret = nom, suite.motif
                break

        routages = [e.routage for e in dossier.etapes.values()]
        if any(not e.resolue for e in dossier.exceptions):
            routages.append(issues.A_REPRENDRE)
        dossier.routage = routage_final(routages)
        return dossier

    def _lancer(self, dossier: Dossier, nom: str) -> Suite:
        etape = Etape(nom=nom, debut=maintenant())
        dossier.etapes[nom] = etape
        dossier.exceptions = [e for e in dossier.exceptions if e.etape != nom or e.resolue]
        debut = time.monotonic()
        tentative = 0
        while True:
            tentative += 1
            etape.tentatives = tentative
            # Une nouvelle tentative repart de zéro : pas d'exceptions en double (essai réel du 04/11)
            dossier.exceptions = [e for e in dossier.exceptions if e.etape != nom or e.resolue]
            try:
                suite = self._etapes[nom](dossier)
                break
            except _Incident as inc:
                etape.appels += inc.appels
                if inc.relancable and tentative <= (self.relances or 0):
                    dossier.noter(nom, "relance", f"tentative {tentative} : {inc.motif}")
                    self.attendre((self.delai or 0) * tentative)
                    continue
                etape.erreur = inc.motif
                suite = Suite(False, issues.INCIDENT, inc.motif, inc.statut)
                break
        etape.statut, etape.routage = suite.statut, suite.routage
        etape.motif = suite.motif
        etape.fin, etape.duree = maintenant(), round(time.monotonic() - debut, 3)
        for a in etape.appels:
            dossier.noter(nom, "appel", {"service": a.service, "url": a.url, "duree": a.duree,
                                         "statut_http": a.statut_http, "erreur": a.erreur})
        if etape.appels:
            etape.requete = etape.requete if etape.requete is not None else etape.appels[-1].requete
            etape.reponse = etape.reponse if etape.reponse is not None else etape.appels[-1].reponse
        return suite

    def _appel(self, dossier: Dossier, nom: str, fonction: Callable, *args: Any) -> Any:
        """Appelle un client ; ServiceIndisponible → incident relançable."""
        try:
            resultat, appel = fonction(*args)
        except ServiceIndisponible as e:
            raise _Incident("service_indisponible", str(e), [e.appel], relancable=e.relancable) from e
        dossier.etapes[nom].appels.append(appel)
        return resultat

    @staticmethod
    def _arret(routage: str, motif: str, statut: str = "arret") -> Suite:
        return Suite(False, routage, motif, statut)

    # ================================================================ étapes
    def _etape_extraction(self, d: Dossier) -> Suite:
        decision = issues.A_REPRENDRE
        try:
            res = extraire(self.services.vlm, d.document.images, d.document.type_document,
                           lecteur_s3=self.services.lire_s3 if self.services.depot else None)
        except ImageIllisible as e:  # dossier vide, image illisible (ING-F-008)
            d.alerter(f"document_invalide:{e}")
            return self._arret(decision, f"{_cfg()['motifs']['document_invalide']} : {e}")
        except ImageInaccessible as e:
            raise _Incident("image_inaccessible", str(e), relancable=True) from e
        except InferenceIndisponible as e:
            statut = "inference_timeout" if e.timeout else "inference_indisponible"
            raise _Incident(statut, str(e), [e.appel], relancable=True) from e
        except ExtractionInvalide as e:
            d.etapes["extraction"].appels += e.appels
            d.etapes["extraction"].reponse = {"bruts": e.bruts}
            rs = routage_service("extraction", "json_invalide")
            return self._arret(rs.routage or issues.INCIDENT, str(e), "json_invalide")

        d.extraction = res
        d.etapes["extraction"].appels += res.appels
        d.noter("extraction", "decision", {"prompt": res.prompt.nom, "empreinte": res.prompt.empreinte,
                                           "strategie": res.strategie, "modele": res.modele})
        d.metadonnees = normaliser_metadonnees(res.extraction)
        d.alerter(*res.alertes)
        if document_vide(d.metadonnees):
            d.alerter("document_invalide:aucune métadonnée extraite")
            return self._arret(decision, f"{_cfg()['motifs']['document_invalide']} : aucune métadonnée du schéma")
        if not d.metadonnees.get("title"):
            # Décision du 05/11 : sans titre, les vérifications ne peuvent pas être faites
            # (alma-checker répond 400) ; document invalide, à vérifier.
            motif = _cfg()["motifs"]["titre_absent"]
            d.alerter(f"document_invalide:{motif}")
            return self._arret(decision, f"{_cfg()['motifs']['document_invalide']} : {motif}")
        self._contexte(d)  # contexte partiel
        return Suite()

    def _etape_provenance(self, d: Dossier) -> Suite:
        m = d.metadonnees or {}
        p = deduire_provenance(m.get("granting_institution"), m.get("co_tutelle_institutions"),
                               d.document.type_document)
        if d.provenance_manuelle:
            p = p.model_copy(update={"valeur": d.provenance_manuelle, "regle": "choix_catalogueur"})
        d.provenance = p
        d.noter("provenance", "decision", {"valeur": p.valeur, "regle": p.regle,
                                           "forme_comparee": p.forme_comparee, "forme_reconnue": p.forme_reconnue})
        if p.a_confirmer and not d.provenance_manuelle:
            motif = _cfg()["motifs"]["provenance"]
            d.exceptions.append(ExceptionDossier(etape="provenance", objet="provenance",
                                                 motif=f"{motif} ({p.valeur} : « {m.get('granting_institution') or ''} »)",
                                                 candidats=["EPHE", "hors EPHE"]))
            return self._arret(issues.A_REPRENDRE, f"{motif} ({p.valeur})")
        return Suite()

    def _etape_alma(self, d: Dossier) -> Suite:
        res = self._appel(d, "alma", verifier_alma, self.services.alma, d.metadonnees, d.document.type_document,
                          self.services.parametres("alma"))
        d.alma = res
        if res.etat is None:
            rs = routage_service("alma", res.statut)
            inc = _cfg()["statut_inconnu"]
            return self._arret(rs.routage or inc["routage"], f"alma-checker : {inc['motif']} « {res.statut} »",
                               "service_indisponible")
        d.etat_alma = d.corrections.get("alma", res.etat)
        d.noter("alma", "decision", {"statut": res.statut, "etat": res.etat, "etat_retenu": d.etat_alma,
                                     "ppn": res.ppn})
        if d.etat_alma != "absent":
            # Au moins une notice trouvée : Sudoc n'est pas interrogé, la situation S01 s'applique.
            d.etapes["sudoc"] = Etape(nom="sudoc", statut="ignoree", motif="notice trouvée dans Alma")
            d.etat_sudoc = _cfg()["etat_sudoc_non_verifie"]
        return Suite()

    def _etape_sudoc(self, d: Dossier) -> Suite:
        parametres = self.services.parametres("sudoc")
        # Même seuil pour le statut du service et l'agrégation des thesis_state
        seuil = parametres.get("duplicate_threshold")
        if seuil is None:
            raise _Incident("service_indisponible", _cfg()["motifs"]["seuil_sudoc"])
        reponse = self._appel(d, "sudoc", verifier_sudoc, self.services.sudoc, d.metadonnees, d.document.type_document,
                              parametres)
        q = agreger(reponse, float(seuil))
        d.sudoc = q
        d.noter("sudoc", "decision", {"etat": q.etat, "justification": q.justification, "ppn_source": q.ppn_source})
        if q.incident:
            return self._arret(issues.INCIDENT, f"sudoc-checker : {q.justification}", "service_indisponible")
        d.etat_sudoc = d.corrections.get("sudoc", q.etat)
        if d.etat_sudoc == charger_yaml("etats_sudoc")["etat_inconnu"]:
            motif = _cfg()["motifs"]["sudoc_indetermine"]
            d.exceptions.append(ExceptionDossier(etape="sudoc", objet="qualification Sudoc",
                                                 motif=f"{motif} : {q.justification}",
                                                 candidats=[c.model_dump() for c in q.candidats if c.retenu]))
            return self._arret(issues.A_REPRENDRE, motif)
        return Suite()

    def _etape_situation(self, d: Dossier) -> Suite:
        faits = faits_du_dossier(d.document.type_document, d.provenance.valeur if d.provenance else None,
                                 d.etat_alma, d.etat_sudoc)
        dec = decider(self.services.regles, faits)
        d.decision = dec
        d.noter("situation", "decision", dec.model_dump(exclude={"ligne"}))
        if dec.suite == "creer":
            return Suite(routage=dec.routage)
        if dec.suite == "convertir":
            return Suite(routage=dec.routage)
        motif = dec.motif
        if d.alma and d.etat_alma != "absent":
            motif += f" — Alma : {d.alma.motif or d.alma.statut}" + (f" (PPN {d.alma.ppn})" if d.alma.ppn else "")
        return self._arret(dec.routage, motif)

    def _etape_conversion(self, d: Dossier) -> Suite:
        dec = d.decision
        if dec is None or dec.suite != "convertir":
            return Suite(routage=issues.TERMINE, statut="ignoree")
        ppn = d.sudoc.ppn_source if d.sudoc else None
        if not ppn:
            return self._arret(issues.INCIDENT, "conversion : aucune notice source retenue", "conversion_echec")
        try:
            xml, appel = lire_notice(self.services.sudoc, ppn)
            d.etapes["conversion"].appels.append(appel)
            iso = unimarc_xml_vers_iso(xml)
        except (ServiceIndisponible, ConversionEchec) as e:
            if isinstance(e, ServiceIndisponible):
                d.etapes["conversion"].appels.append(e.appel)
            return self._arret(issues.INCIDENT, f"conversion : notice source {ppn} illisible ou introuvable ({e})",
                               "conversion_echec")
        d.ppn_source, d.notice_source_iso = ppn, iso
        d.notice_source = iso_vers_winibw(depuis_iso(iso))
        d.notice_convertie = convertir(iso, dec.profil_cible)
        d.alerter(*d.notice_convertie.alertes)
        motif = f"{dec.motif} — notice source {ppn} ({lien_notice(ppn)}), profil cible {dec.profil_cible}"
        d.noter("conversion", "decision", {"ppn_source": ppn, "profil_cible": dec.profil_cible})
        return self._arret(dec.routage, motif, "ok")

    def _etape_idref_personnes(self, d: Dossier) -> Suite:
        m = d.metadonnees or {}
        # Alignements déjà obtenus lors d'une tentative précédente : non rappelés
        # (relance limitée aux personnes en échec, décision du 05/11).
        acquis = self._alignements_acquis.setdefault(d.id, {})
        resultats: dict[str, list] = {}
        for champ in _cfg_contexte()["personnes"]:
            valeurs = m.get(champ)
            bruts = valeurs if isinstance(valeurs, list) else ([valeurs] if valeurs else [])
            resultats[champ] = []
            for i, brut in enumerate(bruts):
                a = acquis.get((champ, i, brut))
                if a is None:
                    a = self._appel(d, "idref_personnes", aligner_personne, self.services.idref, m, brut,
                                    self.services.parametres("idref_personne"))
                    acquis[(champ, i, brut)] = a
                resultats[champ].append(a)
                bloquant = self._statut_idref(d, "idref_personnes", SERVICE_IDREF_PERSONNE, a,
                                              f"{champ}[{i + 1}] {brut}")
                if bloquant:
                    return bloquant
        d.idref_personnes = resultats
        self._alignements_acquis.pop(d.id, None)
        return Suite()

    def _statut_idref(self, d: Dossier, etape: str, service: str, a, objet: str) -> Suite | None:
        rs = routage_service(service, a.statut)
        d.alerter(*(f"{objet} : {x}" for x in rs.alertes))
        if rs.bloquant:
            return self._arret(rs.routage or issues.INCIDENT, f"{service} « {a.statut} » pour {objet}",
                               "service_indisponible" if rs.routage == issues.INCIDENT else "arret")
        if rs.exception:
            d.exceptions.append(ExceptionDossier(etape=etape, objet=objet, motif=f"IdRef : {a.statut}",
                                                 candidats=[c.model_dump() for c in a.candidats]))
        return None

    def _etape_idref_organisations(self, d: Dossier) -> Suite:
        m = d.metadonnees or {}
        if not (m.get("granting_institution") or m.get("doctoral_school") or m.get("co_tutelle_institutions")):
            return Suite()
        orgs = self._appel(d, "idref_organisations", aligner_organisations, self.services.idref, m,
                           self.services.parametres("idref_organisations"))
        d.idref_organisations = orgs
        entites = [("granting_institution", orgs.granting_institution, m.get("granting_institution")),
                   ("doctoral_school", orgs.doctoral_school, m.get("doctoral_school"))]
        entites += [(f"co_tutelle_institutions[{i + 1}]", a, (m.get("co_tutelle_institutions") or [""] * (i + 1))[i])
                    for i, a in enumerate(orgs.co_tutelle_institutions)]
        for champ, a, brut in entites:
            if a is None:
                continue
            bloquant = self._statut_idref(d, "idref_organisations", SERVICE_IDREF_ORGS, a, f"{champ} {brut}")
            if bloquant:
                return bloquant
        return Suite()

    def _etape_dewey(self, d: Dossier) -> Suite:
        res = self._appel(d, "dewey", classer, self.services.dewey, d.metadonnees, self.services.seuil_dewey,
                          self.services.parametres("dewey"))
        d.dewey = res
        rs = routage_service("dewey", res.statut)
        if rs.bloquant:
            return self._arret(rs.routage or issues.INCIDENT, f"dewey-classifier « {res.statut} »")
        if rs.exception:
            d.exceptions.append(ExceptionDossier(etape="dewey", objet="classe Dewey", motif=f"Dewey : {res.statut}",
                                                 candidats=[c.model_dump() for c in res.classes]))
        return Suite()

    def _etape_generation(self, d: Dossier) -> Suite:
        ctx = self._contexte(d)
        profil = d.decision.profil_cible if d.decision else None
        try:
            notice = Generateur(self.services.regles).generer(ctx.valeurs, profil or "")
        except GenerationEchec as e:
            return self._arret(issues.INCIDENT, f"générateur : {e}", "generation_echec")
        d.notice, d.notice_texte = notice, vers_winibw(notice)
        d.alerter(*notice.alertes)
        for z in notice.zones_triees():
            d.noter("generation", "zone", {"zone": z.texte(), "regles": [r.model_dump() for r in z.regles]})
        return Suite()

    # ================================================================ outils
    def _contexte(self, d: Dossier):
        res = ResultatsServices(personnes=d.idref_personnes, organisations=d.idref_organisations,
                                dewey_code=d.dewey.code if d.dewey else None)
        ctx = construire(d.metadonnees or {}, d.document.type_document, res)
        d.contexte = ctx
        d.alerter(*ctx.alertes)
        for t in ctx.transformations:
            if t.avant != t.apres:
                d.noter("contexte", "transformation", t.model_dump())
        return ctx


def services_depuis_reglages(regles: Regles, fournisseur_vlm: str | None = None) -> Services:
    """Services réels configurés par `.env`."""
    from humatheque.extraction.fournisseurs import fournisseur
    from humatheque.ingestion import depot_depuis_reglages

    r = reglages()
    return Services(
        regles=regles,
        vlm=fournisseur(fournisseur_vlm or r.vlm_fournisseur, r),
        alma=ClientService("alma-checker", r.alma_checker_api_url, r.alma_api_key.get_secret_value(), r.http_timeout),
        sudoc=ClientService("sudoc-checker", r.sudoc_checker_api_url, "", r.http_timeout),
        idref=ClientService("idref-linker", r.idref_qualinka_api_url, r.idref_qualinka_api_key.get_secret_value(),
                            r.idref_timeout),
        dewey=ClientService("dewey-classifier", r.dewey_classification_api_url,
                            r.classification_api_key.get_secret_value(), r.http_timeout),
        seuil_dewey=r.dewey_seuil,
        depot=depot_depuis_reglages(r),
    )
