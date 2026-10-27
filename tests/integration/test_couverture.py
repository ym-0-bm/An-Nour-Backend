"""B22 : parcours CRUD peu couverts (admin, scientifique, avis, visiteurs, inscriptions) — API → MongoDB."""
import pytest

from app.config import settings
from tests.integration.test_flows import registration_body

API = settings.API_V1_STR


@pytest.fixture
def admin(seed_user, login):
    return login(seed_user("admin.integ", "admin"))


@pytest.fixture
def sci(seed_user, login):
    return login(seed_user("sci.integ", "scientifique"))


def seminariste_body(**overrides):
    body = {"nom": "Fictif", "prenom": "Manuel", "sexe": "M", "age": 18, "commune_habitation": "Commune fictive",
            "niveau_academique": "Terminale", "dortoir_code": "DTEST", "contact_parent": "0100000000"}
    body.update(overrides)
    return body


def test_health_with_database(api):
    response = api.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "healthy" and response.json()["database"] == "ok"


def test_admin_seminariste_crud(api, admin, seed_dortoir, mongo_db):
    seed_dortoir(capacity=2)
    created = api.post(f"{API}/admin/seminaristes", json=seminariste_body(), headers=admin)
    assert created.status_code == 201, created.text
    matricule = created.json()["matricule"]
    assert mongo_db.dortoirs.find_one({"code": "DTEST"})["current_count"] == 1

    detail = api.get(f"{API}/admin/seminaristes/{matricule}", headers=admin)
    assert detail.status_code == 200 and detail.json()["matricule"] == matricule

    updated = api.put(f"{API}/admin/seminaristes/{matricule}", json={"commune_habitation": "Autre commune"}, headers=admin)
    assert updated.status_code == 200 and updated.json()["commune_habitation"] == "Autre commune"

    assert api.post(f"{API}/admin/seminaristes", json=seminariste_body(sexe="F"), headers=admin).status_code == 400
    assert api.post(f"{API}/admin/seminaristes", json=seminariste_body(dortoir_code="AUCUN"), headers=admin).status_code == 404
    assert api.post(f"{API}/admin/seminaristes", json=seminariste_body(age=3), headers=admin).status_code == 422

    assert api.delete(f"{API}/admin/seminaristes/{matricule}", headers=admin).status_code == 200
    assert api.get(f"{API}/admin/seminaristes/{matricule}", headers=admin).status_code == 404


def test_admin_seminariste_respects_capacity(api, admin, seed_dortoir, mongo_db):
    seed_dortoir(capacity=1)
    assert api.post(f"{API}/admin/seminaristes", json=seminariste_body(), headers=admin).status_code == 201
    assert api.post(f"{API}/admin/seminaristes", json=seminariste_body(prenom="Deux"), headers=admin).status_code == 409
    assert mongo_db.dortoirs.find_one({"code": "DTEST"})["current_count"] == 1


def test_metadata(api, admin, seed_dortoir):
    seed_dortoir()
    api.post(f"{API}/admin/seminaristes", json=seminariste_body(), headers=admin)
    metadata = api.get(f"{API}/admin/metadata", headers=admin).json()
    assert "Terminale" in metadata["niveaux_academiques"]
    assert {"code": "DTEST", "name": "Dortoir DTEST"} in metadata["dortoirs"]
    assert api.get(f"{API}/admin/static-metadata").status_code == 200


def test_membres_co_crud(api, admin):
    body = {"nom": "Membre", "prenoms": "Fictif", "contact": "0100000000", "commission": "Logistique", "statut": "Membre"}
    created = api.post(f"{API}/admin/membres-co", json=body, headers=admin)
    assert created.status_code == 201, created.text
    membre_id = created.json()["id"]

    listing = api.get(f"{API}/admin/membres-co", params={"commission": "Logistique"}, headers=admin).json()
    assert listing["total"] == 1
    assert api.get(f"{API}/admin/membres-co/{membre_id}", headers=admin).json()["nom"].upper() == "MEMBRE"  # stocké en majuscules
    updated = api.put(f"{API}/admin/membres-co/{membre_id}", json={"statut": "Responsable"}, headers=admin)
    assert updated.status_code == 200 and updated.json()["statut"] == "Responsable"
    assert api.delete(f"{API}/admin/membres-co/{membre_id}", headers=admin).status_code == 200
    assert api.get(f"{API}/admin/membres-co/{membre_id}", headers=admin).status_code == 404


def test_scientific_notes_crud_and_lists(api, sci, seed_dortoir):
    seed_dortoir()
    matricule = api.post(f"{API}/registrations", json=registration_body("TX-SCI-1")).json()["matricule"]
    assert api.post(f"{API}/scientific/test-entree", params={"matricule": matricule, "note": 11, "niveau": "Niveau 1"},
                    headers=sci).status_code == 201
    note = api.post(f"{API}/scientific/notes", params={"matricule": matricule, "note": 13, "libelle": "Eval 1"}, headers=sci)
    assert note.status_code == 201
    note_id = note.json()["id"]

    seminaristes = api.get(f"{API}/scientific/seminaristes", params={"search": "FICTIF"}, headers=sci)
    assert seminaristes.status_code == 200 and seminaristes.json()["total"] == 1

    assert api.get(f"{API}/scientific/notes/{note_id}", headers=sci).json()["note"] == 13
    updated = api.put(f"{API}/scientific/notes/{note_id}", json={"note": 15, "observation": "Progrès"}, headers=sci)
    assert updated.status_code == 200 and updated.json()["note"] == 15
    assert api.put(f"{API}/scientific/notes/{note_id}", json={"note": 25}, headers=sci).status_code == 422

    all_notes = api.get(f"{API}/scientific/notes", params={"matricule": matricule}, headers=sci)
    assert all_notes.headers["x-total-count"] == "2"

    stats = api.get(f"{API}/scientific/stats/scientifiques", headers=sci)
    assert stats.status_code == 200, stats.text

    assert api.delete(f"{API}/scientific/notes/{note_id}", headers=sci).status_code == 200
    assert api.get(f"{API}/scientific/notes/{note_id}", headers=sci).status_code == 404
    assert api.get(f"{API}/scientific/bulletins", headers=sci).status_code == 200


def test_formateurs_crud(api, sci):
    created = api.post(f"{API}/scientific/Ajouter-formateur",
                       json={"nom": "Formateur", "prenoms": "Fictif", "contact": "0100000000"}, headers=sci)
    assert created.status_code == 201, created.text
    formateur_id = created.json()["id"]
    assert api.get(f"{API}/scientific/formateurs", headers=sci).json()["total"] == 1
    assert api.get(f"{API}/scientific/{formateur_id}", headers=sci).json()["nom"].upper() == "FORMATEUR"  # stocké en majuscules
    assert api.delete(f"{API}/scientific/{formateur_id}", headers=sci).status_code == 200
    assert api.get(f"{API}/scientific/formateurs", headers=sci).json()["total"] == 0


def test_feedback_flow(api, admin):
    body = {"sexe": "F", "note_globale": 5, "qualite_nourriture": 4, "confort_dortoirs": 3, "qualite_formations": 5,
            "qualite_contenu": 4, "note_organisation": 9, "duree_appropriee": True, "recommande": True,
            "suggestions": "Plus de pauses (fictif)"}
    created = api.post(f"{API}/feedback", json=body)
    assert created.status_code == 200, created.text
    feedback_id = created.json()["id"]
    assert api.post(f"{API}/feedback", json={**body, "note_globale": 6}).status_code == 422

    listing = api.get(f"{API}/admin/feedback", params={"sexe": "F", "recommande": True}, headers=admin).json()
    assert listing["total"] == 1
    analytics = api.get(f"{API}/admin/feedback/analytics", headers=admin)
    assert analytics.status_code == 200, analytics.text
    assert api.get(f"{API}/admin/feedback/{feedback_id}", headers=admin).json()["note_globale"] == 5
    assert api.delete(f"{API}/admin/feedback/{feedback_id}", headers=admin).status_code == 200
    assert api.get(f"{API}/admin/feedback", headers=admin).json()["total"] == 0


def test_visiteurs_detail_and_delete(api, admin):
    created = api.post(f"{API}/visiteurs", json={"nom": "Visiteur", "prenom": "Fictif", "contact": "0100000000"}).json()
    detail = api.get(f"{API}/visiteurs/{created['id']}", headers=admin)
    assert detail.status_code == 200 and detail.json()["nom"].upper() == "VISITEUR"
    assert api.get(f"{API}/visiteurs", params={"search": "visit"}, headers=admin).json()["total"] == 1
    assert api.delete(f"{API}/visiteurs/{created['id']}", headers=admin).status_code == 200
    assert api.get(f"{API}/visiteurs", headers=admin).json()["total"] == 0


def test_registrations_admin_views(api, admin, seed_dortoir, mongo_db):
    seed_dortoir(capacity=5)
    seed_dortoir(code="DFEM", capacity=5, gender="F")
    created = api.post(f"{API}/registrations", json=registration_body("TX-REG-1")).json()
    matricule, reg_id = created["matricule"], created["id"]

    listing = api.get(f"{API}/registrations", params={"search": "FICTIF", "page": 1, "limit": 10}, headers=admin).json()
    assert listing["total"] == 1 and listing["data"][0]["matricule"] == matricule
    assert api.get(f"{API}/registrations/{matricule}", headers=admin).json()["id"] == reg_id
    assert api.get(f"{API}/registrations/{reg_id}", headers=admin).json()["matricule"] == matricule
    assert api.get(f"{API}/registrations/INCONNU", headers=admin).status_code == 404

    updated = api.put(f"{API}/registrations/{reg_id}", json={"allergie": "Lactose (fictif)"}, headers=admin)
    assert updated.status_code == 200
    assert mongo_db.registrations.find_one({"matricule": matricule})["allergie"] == "Lactose (fictif)"

    stats = api.get(f"{API}/registrations/stats/global", headers=admin)
    assert stats.status_code == 200, stats.text

    dortoirs_m = api.get(f"{API}/registrations/dortoirs", params={"sexe": "M"}).json()
    assert {d["code"] for d in dortoirs_m} == {"DTEST"}
    assert api.get(f"{API}/registrations/dortoirs", params={"sexe": "X"}).status_code == 400
    assert {d["code"] for d in api.get(f"{API}/registrations/alldortoirs").json()} == {"DTEST", "DFEM"}

    assert api.delete(f"{API}/registrations/{reg_id}", headers=admin).status_code == 200
    assert mongo_db.registrations.count_documents({}) == 0
