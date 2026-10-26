"""Parcours complets : API → autorisation/validation → persistance MongoDB → réponse."""
from app.config import settings

API = "/api/v1"


def registration_body(transaction_id="TX-INTEG-0001", dortoir="DTEST", nom="Fictif"):
    return {
        "personalInfo": {
            "nom": nom, "prenom": "Seminariste", "sexe": "M", "age": "20",
            "communeHabitation": "Commune fictive", "niveauAcademique": "Licence",
            "contactParent": "0100000000",
        },
        "dormitoryInfo": {"dortoir": f"Dortoir {dortoir}", "dortoirId": dortoir},
        "healthInfo": {"allergie": "RAS", "antecedentMedical": "Néant"},
        "paymentInfo": {"transactionId": transaction_id, "amount": 1},
    }


def test_login_and_role_based_access(api, seed_user, login, mongo_db):
    admin = seed_user("admin.integ", "admin")
    sante = seed_user("sante.integ", "sante")

    assert api.post(f"{API}/admin/auth/login", json={**admin, "password": "mauvais"}).status_code == 401

    admin_headers = login(admin)
    me = api.get(f"{API}/admin/auth/me", headers=admin_headers)
    assert me.status_code == 200 and me.json()["username"] == "admin.integ"
    assert "password" not in me.json()
    assert mongo_db.users.find_one({"username": "admin.integ"})["last_login"] is not None

    users = api.get(f"{API}/admin/users", headers=admin_headers)
    assert users.status_code == 200
    assert {u["username"] for u in users.json()} == {"admin.integ", "sante.integ"}

    sante_headers = login(sante)
    assert api.get(f"{API}/admin/users", headers=sante_headers).status_code == 403
    assert api.get(f"{API}/finances/dashboard", headers=sante_headers).status_code == 403


def test_deactivated_account_is_rejected_immediately(api, seed_user, login, mongo_db):
    admin = seed_user("admin.integ", "admin")
    other = seed_user("autre.integ", "scientifique")
    admin_headers, other_headers = login(admin), login(other)

    other_id = str(mongo_db.users.find_one({"username": "autre.integ"})["_id"])
    toggled = api.patch(f"{API}/admin/users/{other_id}", headers=admin_headers)
    assert toggled.status_code == 200 and toggled.json()["is_active"] is False
    assert mongo_db.users.find_one({"username": "autre.integ"})["is_active"] is False

    assert api.get(f"{API}/admin/auth/me", headers=other_headers).status_code == 403


def test_public_registration_creates_finance_entry_and_rejects_reused_receipt(api, seed_dortoir, mongo_db):
    seed_dortoir(capacity=10)

    response = api.post(f"{API}/registrations", json=registration_body())
    assert response.status_code == 201, response.text
    matricule = response.json()["matricule"]

    registration = mongo_db.registrations.find_one({"matricule": matricule})
    assert registration["transaction_id"] == "TX-INTEG-0001"
    assert mongo_db.dortoirs.find_one({"code": "DTEST"})["current_count"] == 1

    # Écriture financière au tarif du serveur, jamais au montant envoyé par le client (1)
    entry = mongo_db.transactions.find_one({"matricule": matricule})
    assert entry["type"] == "ENTREE"
    assert entry["montant"] == float(settings.REGISTRATION_FEE)

    duplicate = api.post(f"{API}/registrations", json=registration_body(nom="Autre"))
    assert duplicate.status_code == 409
    assert mongo_db.registrations.count_documents({}) == 1
    assert mongo_db.dortoirs.find_one({"code": "DTEST"})["current_count"] == 1


def test_full_dormitory_is_refused(api, seed_dortoir, mongo_db):
    seed_dortoir(capacity=1)
    assert api.post(f"{API}/registrations", json=registration_body("TX-INTEG-A")).status_code == 201
    full = api.post(f"{API}/registrations", json=registration_body("TX-INTEG-B"))
    assert full.status_code == 409
    assert mongo_db.registrations.count_documents({}) == 1


def test_notes_flow_records_author_from_token(api, seed_user, seed_dortoir, login, mongo_db):
    seed_dortoir()
    matricule = api.post(f"{API}/registrations", json=registration_body()).json()["matricule"]
    headers = login(seed_user("sci.integ", "scientifique"))

    entree = api.post(f"{API}/scientific/test-entree",
                      params={"matricule": matricule, "note": 12, "niveau": "Niveau 1", "created_by": "usurpateur"},
                      headers=headers)
    assert entree.status_code == 201, entree.text

    note = api.post(f"{API}/scientific/notes", params={"matricule": matricule, "note": 16, "libelle": "Evaluation 1"},
                    headers=headers)
    assert note.status_code == 201, note.text
    assert api.post(f"{API}/scientific/notes", params={"matricule": matricule, "note": 25}, headers=headers).status_code == 400

    stored = list(mongo_db.notes.find({"matricule": matricule}))
    assert sorted(n["type"] for n in stored) == ["EVALUATION", "TEST_ENTREE"]
    assert {n["created_by"] for n in stored} == {"sci.integ"}

    listed = api.get(f"{API}/scientific/seminaristes/{matricule}/notes", headers=headers)
    assert listed.status_code == 200
    assert sorted(n["note"] for n in listed.json()) == [12, 16]


def test_finance_donation_is_audited_with_token_author(api, seed_user, login, mongo_db):
    headers = login(seed_user("fin.integ", "finances"))

    created = api.post(f"{API}/finances/entrees/don", params={"created_by": "usurpateur"},
                       json={"montant": 2500, "donateur": "Donateur Fictif"}, headers=headers)
    assert created.status_code == 201, created.text
    reference = created.json()["reference"]

    transaction = mongo_db.transactions.find_one({"reference": reference})
    assert transaction["created_by"] == "fin.integ"
    # transaction_id est un String dans le schéma Prisma (pas un ObjectId)
    audit = mongo_db.audit_logs.find_one({"transaction_id": str(transaction["_id"])})
    assert audit["action"] == "CREATE" and audit["modified_by"] == "fin.integ"

    listed = api.get(f"{API}/finances/entrees", headers=headers)
    assert listed.status_code == 200
    assert reference in listed.text

    assert api.post(f"{API}/finances/entrees/don", json={"montant": -5, "donateur": "X"}, headers=headers).status_code == 422
