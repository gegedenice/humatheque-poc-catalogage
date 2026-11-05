"""Clients des services (SPEC §6), mocks respx tirés des réponses réelles."""

import httpx
import pytest
import respx

import services_simules as sim
from humatheque.services import ClientService, ServiceIndisponible
from humatheque.services.alma import payload_alma, verifier_alma
from humatheque.services.dewey import classer
from humatheque.services.idref import aligner_organisations, aligner_personne
from humatheque.services.sudoc import lire_notice, payload_sudoc, verifier_sudoc

META = {
    "title": "La question de l'hygiène aux Indes-Néerlandaises",
    "subtitle": "Les enjeux médicaux, culturels et sociaux",
    "author": "Gani Achmad JAE LANI",
    "degree_type": "Thèse de doctorat",
    "discipline": "Histoire et civilisations",
    "granting_institution": "École des Hautes Études en Sciences Sociales",
    "co_tutelle_institutions": [],
    "doctoral_school": "École doctorale de l'EHESS",
    "defense_year": "2017",
    "advisor": ["Gérard JORLAND"],
    "jury_president": None,
    "reviewers": [],
    "committee_members": ["Romain BERTRAND", "Patrice BOURDELAIS"],
    "language": "fre",
    "confidence": None,
}


def _client(nom, url, **kw):
    return ClientService(nom, url, **kw)


@respx.mock
@pytest.mark.parametrize("type_document, route", [("these", "/check/thesis"), ("memoire", "/check/dissertation")])
def test_alma_route_selon_type_document(type_document, route):  # ING-T-005
    r = respx.post(f"{sim.ALMA_URL}{route}").respond(json=sim.fixture("alma"))
    res, appel = verifier_alma(_client("alma-checker", sim.ALMA_URL), META, type_document)
    assert r.called
    assert (res.statut, res.etat, res.ppn) == ("academic_record_found", "présent", "229969437")
    assert res.candidats[0].type == "Thèse de doctorat" and res.candidats[0].annee == "2017"
    assert appel.requete["advisor"] == ["Gérard JORLAND"]


@respx.mock
@pytest.mark.parametrize("statut, etat", [
    ("academic_record_found", "présent"), ("ambiguous_academic_candidate", "incertain"),
    ("off_profile_match_only", "incertain"), ("no_academic_record_found", "absent"),
    ("statut_futur", None),
])
def test_alma_etats(statut, etat):
    respx.post(f"{sim.ALMA_URL}/check/thesis").respond(json=sim.alma(statut, ppn="111"))
    res, _ = verifier_alma(_client("alma-checker", sim.ALMA_URL), META, "these")
    assert res.etat == etat


def test_payloads():
    a, s = payload_alma(META), payload_sudoc(META)
    assert a["committee_members"] == ["Romain BERTRAND", "Patrice BOURDELAIS"]
    assert s["committee_members"] == "Romain BERTRAND | Patrice BOURDELAIS"
    assert s["jury_president"] == "" and "confidence" not in s


@respx.mock
def test_sudoc_et_cle_api_facultative():
    r = respx.post(f"{sim.SUDOC_URL}/check/dissertation").respond(json=sim.fixture("sudoc"))
    reponse, appel = verifier_sudoc(_client("sudoc-checker", sim.SUDOC_URL, cle_api="k"), META, "memoire")
    assert reponse["status"] == "electronic_only"
    assert r.calls[0].request.headers["X-API-Key"] == "k"
    respx.post(f"{sim.SUDOC_URL}/check/thesis").respond(json={})
    verifier_sudoc(_client("sudoc-checker", sim.SUDOC_URL), META, "these")
    assert "X-API-Key" not in respx.calls[-1].request.headers


@respx.mock
def test_lire_notice_sudoc():
    respx.get("https://www.sudoc.fr/229969437.xml").respond(text=sim.notice_xml())
    xml, appel = lire_notice(_client("sudoc", sim.SUDOC_URL), "229969437")
    assert "<controlfield tag=\"001\">229969437</controlfield>" in xml


@respx.mock
@pytest.mark.parametrize("panne, timeout", [
    (httpx.ConnectError("refusé"), False),
    (httpx.ReadTimeout("lent"), True),
    (httpx.Response(500, text="boum"), False),
    (httpx.Response(400, json={"detail": "Unable to build Sudoc query"}), False),
    (httpx.Response(200, text="pas du json"), False),
])
def test_service_indisponible(panne, timeout):
    route = respx.post(f"{sim.SUDOC_URL}/check/thesis")
    if isinstance(panne, Exception):
        route.side_effect = panne
    else:
        route.return_value = panne
    with pytest.raises(ServiceIndisponible) as e:
        verifier_sudoc(_client("sudoc-checker", sim.SUDOC_URL), META, "these")
    assert e.value.timeout is timeout
    assert e.value.appel.erreur and e.value.appel.requete["title"]


@respx.mock
def test_idref_personne():
    r = respx.post(f"{sim.IDREF_URL}/align/person").respond(json=sim.fixture("idref"))
    res, appel = aligner_personne(_client("idref", sim.IDREF_URL), META, "Gérard JORLAND")
    assert (res.statut, res.ppn) == ("low_confidence", None)
    assert res.candidats[0].ppn == "026940345"
    assert appel.requete["name"] == "Gérard JORLAND" and appel.requete["institution"].startswith("École")
    assert "first_name" not in appel.requete and "last_name" not in appel.requete


@respx.mock
def test_idref_organisations():
    respx.post(f"{sim.IDREF_URL}/align/thesis-organizations").respond(json=sim.idref_organisations())
    res, _ = aligner_organisations(_client("idref", sim.IDREF_URL), META)
    assert (res.granting_institution.statut, res.granting_institution.ppn) == ("accepted", "026375478")
    assert res.co_tutelle_institutions[0].statut == "low_confidence"
    assert res.doctoral_school.ppn == "169661717"


@respx.mock
@pytest.mark.parametrize("seuil, statut, code", [(None, "au_dessus_seuil", "900"), (0.5, "au_dessus_seuil", "900"),
                                                 (0.9, "sous_seuil", None)])
def test_dewey(seuil, statut, code):
    r = respx.post(f"{sim.DEWEY_URL}/classify").respond(json=sim.dewey())
    res, _ = classer(_client("dewey", sim.DEWEY_URL), META, seuil)
    assert (res.statut, res.code) == (statut, code)
    assert "Histoire et civilisations" in r.calls[0].request.content.decode()
