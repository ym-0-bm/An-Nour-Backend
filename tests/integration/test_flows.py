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


def verify_payment(api, headers, matricule, transaction_id="TX-INTEG-0001", motif="Relevé Wave contrôlé (fictif)"):
    """Validation manuelle du paiement (admin ou finances)."""
    response = api.patch(f"{API}/registrations/{matricule}/paiement",
                         json={"decision": "verifie", "transaction_id": transaction_id, "motif": motif}, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def register_and_verify(api, headers, transaction_id="TX-INTEG-0001", **kwargs):
    """Inscription publique puis validation du paiement : entrée financière créée."""
    response = api.post(f"{API}/registrations", json=registration_body(transaction_id, **kwargs))
    assert response.status_code == 201, response.text
    matricule = response.json()["matricule"]
    verify_payment(api, headers, matricule, transaction_id)
    return matricule


def test_registration_is_pending_until_payment_verified(api, seed_dortoir, seed_user, login, mongo_db):
    seed_dortoir(capacity=10)
    fin = login(seed_user("fin.integ", "finances"))

    response = api.post(f"{API}/registrations", json=registration_body())
    assert response.status_code == 201, response.text
    matricule = response.json()["matricule"]
    assert response.json()["payment_status"] == "en_attente"

    registration = mongo_db.registrations.find_one({"matricule": matricule})
    assert registration["transaction_id"] == "TX-INTEG-0001"
    assert mongo_db.dortoirs.find_one({"code": "DTEST"})["current_count"] == 1
    # B11 : aucune entrée financière tant que le paiement n'est pas vérifié
    assert mongo_db.transactions.count_documents({"matricule": matricule}) == 0

    verify_payment(api, fin, matricule)
    # Écriture financière au tarif du serveur, jamais au montant envoyé par le client (1)
    entry = mongo_db.transactions.find_one({"matricule": matricule})
    assert entry["type"] == "ENTREE"
    assert entry["montant"] == float(settings.REGISTRATION_FEE)

    detail = api.get(f"{API}/registrations/{matricule}/paiement", headers=fin).json()
    assert (detail["statut"], detail["methode"], detail["verified_by"]) == ("verifie", "manuel", "fin.integ")
    assert detail["historique"][0]["motif"] == "Relevé Wave contrôlé (fictif)"

    # Transaction déjà utilisée : refusée dès la déclaration
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
    # T8 : AuditLog.transaction_id est un ObjectId, comme Transaction.id (jointure correcte)
    audit = mongo_db.audit_logs.find_one({"transaction_id": transaction["_id"]})
    assert audit["action"] == "CREATE" and audit["modified_by"] == "fin.integ"

    listed = api.get(f"{API}/finances/entrees", headers=headers)
    assert listed.status_code == 200
    assert reference in listed.text

    assert api.post(f"{API}/finances/entrees/don", json={"montant": -5, "donateur": "X"}, headers=headers).status_code == 422
