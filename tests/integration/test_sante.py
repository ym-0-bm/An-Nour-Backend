"""Module Santé (Partie 6) : fiche médicale, consultations, traçabilité — API → MongoDB."""
from datetime import datetime, timedelta, timezone

import pytest
from bson import ObjectId

from app.config import settings
from tests.integration.test_flows import registration_body

API = f"{settings.API_V1_STR}/sante"
MOTIF = "Céphalées depuis deux jours (fictif)"


@pytest.fixture
def patient(api, seed_dortoir):
    seed_dortoir()
    body = registration_body()
    body["personalInfo"]["dateNaissance"] = "2006-04-12"
    response = api.post(f"{settings.API_V1_STR}/registrations", json=body)
    assert response.status_code == 201, response.text
    return response.json()["matricule"]


@pytest.fixture
def sante(seed_user, login):
    return login(seed_user("sante.integ", "sante"))


@pytest.fixture
def admin(seed_user, login):
    return login(seed_user("admin.integ", "admin"))


def user_id(mongo_db, username):
    return str(mongo_db.users.find_one({"username": username})["_id"])


def consultation_body(**overrides):
    body = {
        "date_consultation": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
        "motif_consultation": MOTIF,
        "observations_diagnostic": "Examen normal, probable fatigue (fictif)",
        "traitement_prescrit": "Repos et hydratation (fictif)",
    }
    body.update(overrides)
    return body


def create_consultation(api, headers, matricule, **overrides):
    response = api.post(f"{API}/patients/{matricule}/consultations", json=consultation_body(**overrides), headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


# ---------------------------------------------------------------------------
# Fiche médicale
# ---------------------------------------------------------------------------

def test_fiche_medicale_maps_existing_data(api, sante, patient, seed_user, login):
    fiche = api.get(f"{API}/patients/{patient}/fiche-medicale", headers=sante)
    assert fiche.status_code == 200, fiche.text
    body = fiche.json()
    assert body["patient"]["nom_prenoms"] == "FICTIF SEMINARISTE"
    assert body["patient"]["date_naissance"] == "2006-04-12"
    assert body["patient"]["niveau_scolaire"] == "Licence"     # niveau académique de l'inscription
    assert body["patient"]["niveau"] is None                    # pas encore de test d'entrée
    assert body["patient"]["dortoir"] == "Dortoir DTEST" and body["patient"]["sexe"] == "M"
    assert body["informations_medicales"] == {"contact_parent": "0100000000", "allergies": "RAS", "antecedents_medicaux": "Néant"}
    assert body["nombre_consultations"] == 0

    # Après le test d'entrée, "niveau" = niveau du séminaire
    sci = login(seed_user("sci.integ", "scientifique"))
    assert api.post(f"{settings.API_V1_STR}/scientific/test-entree",
                    params={"matricule": patient, "note": 12, "niveau": "Niveau 2"}, headers=sci).status_code == 201
    assert api.get(f"{API}/patients/{patient}/fiche-medicale", headers=sante).json()["patient"]["niveau"] == "Niveau 2"


def test_unknown_patient_returns_404(api, sante):
    assert api.get(f"{API}/patients/INCONNU/fiche-medicale", headers=sante).status_code == 404
    assert api.get(f"{API}/patients/INCONNU/consultations", headers=sante).status_code == 404
    assert api.post(f"{API}/patients/INCONNU/consultations", json=consultation_body(), headers=sante).status_code == 404


def test_update_informations_medicales(api, sante, patient, mongo_db):
    response = api.patch(f"{API}/patients/{patient}/informations-medicales", json={
        "allergies": "Pénicilline (fictif)", "antecedents_medicaux": "Asthme (fictif)",
        "contact_parent": "0700000000", "date_naissance": "2005-01-31",
    }, headers=sante)
    assert response.status_code == 200, response.text
    assert response.json()["informations_medicales"] == {
        "contact_parent": "0700000000", "allergies": "Pénicilline (fictif)", "antecedents_medicaux": "Asthme (fictif)"}
    stored = mongo_db.registrations.find_one({"matricule": patient})
    assert stored["allergie"] == "Pénicilline (fictif)" and stored["contact_parent"] == "0700000000"

    # Les champs facultatifs peuvent être vidés
    cleared = api.patch(f"{API}/patients/{patient}/informations-medicales", json={"allergies": None}, headers=sante)
    assert cleared.json()["informations_medicales"]["allergies"] is None


@pytest.mark.parametrize("payload", [
    {}, {"contact_parent": None}, {"contact_parent": "0" * 31}, {"date_naissance": "2999-01-01"}, {"date_naissance": "1800-01-01"},
])
def test_update_informations_medicales_validation(api, sante, patient, payload):
    assert api.patch(f"{API}/patients/{patient}/informations-medicales", json=payload, headers=sante).status_code == 422


def test_registration_rejects_future_birth_date(api, seed_dortoir):
    seed_dortoir()
    body = registration_body()
    body["personalInfo"]["dateNaissance"] = "2999-01-01"
    assert api.post(f"{settings.API_V1_STR}/registrations", json=body).status_code == 422


# ---------------------------------------------------------------------------
# Consultations
# ---------------------------------------------------------------------------

def test_sante_user_is_default_medecin(api, sante, patient, mongo_db):
    consultation = create_consultation(api, sante, patient)
    assert consultation["medecin_responsable_id"] == user_id(mongo_db, "sante.integ")
    assert consultation["medecin_responsable"] == "Sante.integ Fictif"
    assert consultation["created_by"] == "sante.integ"
    assert consultation["matricule"] == patient


def test_admin_must_designate_a_valid_medecin(api, admin, sante, patient, mongo_db, seed_user):
    assert api.post(f"{API}/patients/{patient}/consultations", json=consultation_body(), headers=admin).status_code == 422

    medecin = user_id(mongo_db, "sante.integ")
    created = api.post(f"{API}/patients/{patient}/consultations",
                       json=consultation_body(medecin_responsable_id=medecin), headers=admin)
    assert created.status_code == 201 and created.json()["medecin_responsable_id"] == medecin

    seed_user("sci.integ", "scientifique")
    seed_user("sante.inactif", "sante", is_active=False)
    for invalid in (user_id(mongo_db, "sci.integ"), user_id(mongo_db, "sante.inactif"), "pas-un-objectid", str(ObjectId())):
        response = api.post(f"{API}/patients/{patient}/consultations",
                            json=consultation_body(medecin_responsable_id=invalid), headers=admin)
        assert response.status_code == 422, invalid


@pytest.mark.parametrize("override", [
    {"date_consultation": (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()},
    {"motif_consultation": "   "},
    {"traitement_prescrit": "x" * 2001},
])
def test_consultation_validation(api, sante, patient, override):
    response = api.post(f"{API}/patients/{patient}/consultations", json=consultation_body(**override), headers=sante)
    assert response.status_code == 422


def test_history_is_kept_and_ordered(api, sante, patient):
    older = create_consultation(api, sante, patient, date_consultation=(datetime.now(timezone.utc) - timedelta(days=3)).isoformat())
    newer = create_consultation(api, sante, patient)

    history = api.get(f"{API}/patients/{patient}/consultations", headers=sante).json()
    assert history["total"] == 2
    assert [c["id"] for c in history["data"]] == [newer["id"], older["id"]]
    page2 = api.get(f"{API}/patients/{patient}/consultations", params={"page": 2, "limit": 1}, headers=sante).json()
    assert [c["id"] for c in page2["data"]] == [older["id"]]
    assert api.get(f"{API}/patients/{patient}/fiche-medicale", headers=sante).json()["nombre_consultations"] == 2


def test_get_update_and_export_consultation(api, sante, patient):
    consultation = create_consultation(api, sante, patient)
    cid = consultation["id"]

    assert api.get(f"{API}/consultations/{cid}", headers=sante).json()["motif_consultation"] == MOTIF

    updated = api.patch(f"{API}/consultations/{cid}", json={"traitement_prescrit": "Paracétamol (fictif)",
                                                            "matricule": "AUTRE"}, headers=sante)
    assert updated.status_code == 200
    assert updated.json()["traitement_prescrit"] == "Paracétamol (fictif)"
    assert updated.json()["matricule"] == patient  # le patient rattaché ne change jamais

    export = api.get(f"{API}/consultations/{cid}/export", headers=sante)
    assert export.status_code == 200
    assert export.json()["consultation"]["id"] == cid
    assert export.json()["fiche"]["patient"]["matricule"] == patient


@pytest.mark.parametrize("payload", [{}, {"motif_consultation": None}, {"date_consultation": None},
                                     {"medecin_responsable_id": None}])
def test_update_consultation_validation(api, sante, patient, payload):
    cid = create_consultation(api, sante, patient)["id"]
    assert api.patch(f"{API}/consultations/{cid}", json=payload, headers=sante).status_code == 422


@pytest.mark.parametrize("cid", ["pas-un-id", str(ObjectId())])
def test_unknown_consultation_returns_404(api, sante, cid):
    assert api.get(f"{API}/consultations/{cid}", headers=sante).status_code == 404
    assert api.patch(f"{API}/consultations/{cid}", json={"traitement_prescrit": "x"}, headers=sante).status_code == 404
    assert api.get(f"{API}/consultations/{cid}/export", headers=sante).status_code == 404


def test_no_deletion_of_medical_history(api, sante, patient):
    cid = create_consultation(api, sante, patient)["id"]
    assert api.delete(f"{API}/consultations/{cid}", headers=sante).status_code == 405


# ---------------------------------------------------------------------------
# Autorisations et traçabilité
# ---------------------------------------------------------------------------

def test_admin_has_full_access(api, admin, sante, patient, mongo_db):
    medecin = user_id(mongo_db, "sante.integ")
    cid = api.post(f"{API}/patients/{patient}/consultations",
                   json=consultation_body(medecin_responsable_id=medecin), headers=admin).json()["id"]
    assert api.get(f"{API}/patients/{patient}/fiche-medicale", headers=admin).status_code == 200
    assert api.patch(f"{API}/consultations/{cid}", json={"traitement_prescrit": "Modifié (fictif)"}, headers=admin).status_code == 200


@pytest.mark.parametrize("role", ["scientifique", "finances"])
def test_other_roles_are_refused(api, patient, seed_user, login, role):
    headers = login(seed_user(f"{role}.integ", role))
    assert api.get(f"{API}/patients/{patient}/fiche-medicale", headers=headers).status_code == 403
    assert api.post(f"{API}/patients/{patient}/consultations", json=consultation_body(), headers=headers).status_code == 403


def test_every_access_is_traced_without_medical_content(api, sante, patient, mongo_db):
    cid = create_consultation(api, sante, patient)["id"]
    api.get(f"{API}/patients/{patient}/fiche-medicale", headers=sante)
    api.get(f"{API}/patients/{patient}/consultations", headers=sante)
    api.get(f"{API}/consultations/{cid}", headers=sante)
    api.patch(f"{API}/consultations/{cid}", json={"traitement_prescrit": "Secret médical (fictif)"}, headers=sante)
    api.get(f"{API}/consultations/{cid}/export", headers=sante)
    api.patch(f"{API}/patients/{patient}/informations-medicales", json={"allergies": "Arachide (fictif)"}, headers=sante)

    logs = list(mongo_db.medical_access_logs.find({"matricule": patient}))
    assert sorted(log["action"] for log in logs) == sorted(
        ["CREATE", "READ_FICHE", "LIST_CONSULTATIONS", "READ_CONSULTATION", "UPDATE", "EXPORT", "UPDATE_INFOS"])
    assert {log["username"] for log in logs} == {"sante.integ"}
    update_log = next(log for log in logs if log["action"] == "UPDATE")
    assert update_log["fields_changed"] == ["traitement_prescrit"] and update_log["consultation_id"] == cid

    # Aucune valeur médicale dans les journaux
    dump = str(logs)
    for medical_value in (MOTIF, "Secret médical", "Arachide"):
        assert medical_value not in dump

    # Un refus d'accès ne produit pas de lecture tracée
    before = mongo_db.medical_access_logs.count_documents({})
    api.get(f"{API}/patients/{patient}/fiche-medicale")
    assert mongo_db.medical_access_logs.count_documents({}) == before
