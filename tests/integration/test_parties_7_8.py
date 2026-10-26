"""Parties 7 et 8 (B5 à B13) et statistiques santé : parcours API → MongoDB."""
import io
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pytest
from openpyxl import load_workbook
from PIL import Image

from app.config import settings
from app.routes import registrations as registrations_module
from app.utils.matricule_generator import matricule_prefix
from tests.integration.test_flows import registration_body, verify_payment

API = settings.API_V1_STR


def png():
    buffer = io.BytesIO()
    Image.new("RGB", (20, 20), "white").save(buffer, format="PNG")
    return buffer.getvalue()


def receipt_text(transaction_id="TABCDEFGHJKLMNPQR", montant="6.000"):
    return f"-{montant}F\nPaiement AN-NOUR\nStatut Effectué\nEn partenariat avec UBA\nID de transaction\n{transaction_id}"


@pytest.fixture
def fake_ocr(monkeypatch):
    """Seul l'OCR est simulé : le véritable analyseur examine le texte renvoyé."""
    state = {"text": receipt_text(), "error": False}

    def process_image(content):
        if state["error"]:
            raise RuntimeError("OCR indisponible (fictif)")
        return state["text"]

    monkeypatch.setattr(registrations_module.ocr_processor, "process_image", process_image)
    return state


@pytest.fixture
def pending(api, seed_dortoir):
    """Inscription en attente : (matricule, jeton)."""
    seed_dortoir(capacity=50)

    def create(transaction_id="TX-DECLARE-1", **kwargs):
        response = api.post(f"{API}/registrations", json=registration_body(transaction_id, **kwargs))
        assert response.status_code == 201, response.text
        return response.json()["matricule"], response.json()["verification_token"]

    return create


def submit(api, matricule, token):
    return api.post(f"{API}/registrations/{matricule}/receipt", data={"token": token},
                    files={"file": ("recu.png", png(), "image/png")})


@pytest.fixture
def fin(seed_user, login):
    return login(seed_user("fin.integ", "finances"))


# ---------------------------------------------------------------------------
# B9 / B11 : vérification obligatoire du reçu
# ---------------------------------------------------------------------------

def test_receipt_verified_creates_finance_entry_once(api, pending, fake_ocr, mongo_db):
    matricule, token = pending()
    result = submit(api, matricule, token)
    assert result.status_code == 200, result.text
    assert result.json()["statut"] == "verifie"

    registration = mongo_db.registrations.find_one({"matricule": matricule})
    assert registration["payment_status"] == "verifie" and registration["payment_method"] == "ocr"
    assert registration["transaction_id"] == "TABCDEFGHJKLMNPQR"  # ID lu sur le reçu, pas l'ID déclaré
    assert mongo_db.used_transactions.find_one({"transaction_id": "TABCDEFGHJKLMNPQR"})["matricule"] == matricule
    entry = mongo_db.transactions.find_one({"matricule": matricule})
    assert entry["montant"] == float(settings.REGISTRATION_FEE)
    assert mongo_db.payment_verifications.count_documents({"matricule": matricule, "statut": "verifie"}) == 1

    # Impossible de revalider : pas de double écriture financière
    assert submit(api, matricule, token).status_code == 409
    assert mongo_db.transactions.count_documents({"matricule": matricule}) == 1


def test_same_receipt_cannot_validate_two_registrations(api, pending, fake_ocr, mongo_db):
    first, token1 = pending("TX-DECLARE-A")
    second, token2 = pending("TX-DECLARE-B")
    assert submit(api, first, token1).json()["statut"] == "verifie"

    result = submit(api, second, token2).json()
    assert result["statut"] == "rejete"
    assert "déjà été utilisée" in result["message"]
    assert mongo_db.registrations.find_one({"matricule": second})["payment_status"] == "rejete"
    assert mongo_db.transactions.count_documents({"matricule": second}) == 0


@pytest.mark.parametrize("text,expected_message", [
    (receipt_text(montant="5.000"), "Montant invalide"),
    (receipt_text(transaction_id=""), None),  # A1 : sans ID de transaction, jamais vérifié
])
def test_invalid_receipt_is_rejected_and_can_be_retried(api, pending, fake_ocr, mongo_db, text, expected_message):
    matricule, token = pending()
    fake_ocr["text"] = text
    result = submit(api, matricule, token).json()
    assert result["statut"] == "rejete"
    if expected_message:
        assert expected_message in result["message"]
    assert mongo_db.transactions.count_documents({"matricule": matricule}) == 0

    fake_ocr["text"] = receipt_text()  # nouvel essai avec un reçu valide
    assert submit(api, matricule, token).json()["statut"] == "verifie"


def test_ocr_failure_is_reported_as_error(api, pending, fake_ocr, mongo_db):
    matricule, token = pending()
    fake_ocr["error"] = True
    result = submit(api, matricule, token)
    assert result.status_code == 200 and result.json()["statut"] == "erreur"
    assert "OCR indisponible" not in result.text  # aucun détail interne
    assert mongo_db.registrations.find_one({"matricule": matricule})["payment_status"] == "erreur"


REAL_TOKEN = object()


@pytest.mark.parametrize("token,matricule_ok", [("mauvais-jeton", True), ("", True), (REAL_TOKEN, False)])
def test_receipt_requires_the_registration_token(api, pending, fake_ocr, token, matricule_ok):
    matricule, real_token = pending()
    target = matricule if matricule_ok else "ANNOUR99-INCONNU-001"
    sent = real_token if token is REAL_TOKEN else token
    response = api.post(f"{API}/registrations/{target}/receipt", data={"token": sent},
                        files={"file": ("recu.png", png(), "image/png")})
    if token == "":
        assert response.status_code == 422
    else:
        assert response.status_code == 403
        assert response.json()["detail"] == "Matricule ou jeton de vérification invalide"


def test_legacy_registration_has_no_token_and_is_historically_verified(api, fin, mongo_db, pending, fake_ocr):
    matricule, _ = pending()
    mongo_db.registrations.update_one({"matricule": matricule},
                                      {"$set": {"payment_status": "completed"}, "$unset": {"receipt_token_hash": ""}})
    assert api.get(f"{API}/registrations/{matricule}/paiement", headers=fin).json()["statut"] == "verifie_historique"
    assert submit(api, matricule, "nimporte-quel-jeton").status_code == 403
    assert api.patch(f"{API}/registrations/{matricule}/paiement", headers=fin,
                     json={"decision": "rejete", "motif": "Essai"}).status_code == 409


@pytest.mark.parametrize("payload", [
    {"decision": "verifie", "motif": "Sans transaction"},
    {"decision": "verifie", "transaction_id": "TX-1", "motif": ""},
    {"decision": "annule", "motif": "Décision inconnue"},
])
def test_manual_decision_validation(api, fin, pending, payload):
    matricule, _ = pending()
    assert api.patch(f"{API}/registrations/{matricule}/paiement", json=payload, headers=fin).status_code == 422


def test_manual_rejection_then_validation(api, fin, pending, mongo_db):
    matricule, _ = pending()
    rejected = api.patch(f"{API}/registrations/{matricule}/paiement", headers=fin,
                         json={"decision": "rejete", "motif": "Montant incomplet (fictif)"})
    assert rejected.json()["statut"] == "rejete"
    verify_payment(api, fin, matricule, "TX-MANUEL-1")
    history = api.get(f"{API}/registrations/{matricule}/paiement", headers=fin).json()["historique"]
    assert [(h["statut"], h["verified_by"]) for h in history] == [("verifie", "fin.integ"), ("rejete", "fin.integ")]
    assert api.patch(f"{API}/registrations/{matricule}/paiement", headers=fin,
                     json={"decision": "verifie", "transaction_id": "TX-AUTRE", "motif": "Doublon"}).status_code == 409


def test_unique_reservation_blocks_concurrent_validation(api, fin, pending, mongo_db):
    # Réservation déjà prise par une validation concurrente, dont l'inscription n'est pas encore mise à jour :
    # seul l'index unique du registre des transactions peut bloquer ce cas
    matricule, _ = pending()
    mongo_db.used_transactions.insert_one(
        {"transaction_id": "TX-EN-COURS", "matricule": "ANNOUR26-AUTRE-001", "created_at": datetime.now(timezone.utc)})
    response = api.patch(f"{API}/registrations/{matricule}/paiement", headers=fin,
                         json={"decision": "verifie", "transaction_id": "TX-EN-COURS", "motif": "Essai"})
    assert response.status_code == 409
    assert mongo_db.registrations.find_one({"matricule": matricule})["payment_status"] == "en_attente"


def test_manual_validation_refuses_used_transaction(api, fin, pending):
    first, _ = pending("TX-DECLARE-A")
    second, _ = pending("TX-DECLARE-B")
    verify_payment(api, fin, first, "TX-PARTAGE")
    response = api.patch(f"{API}/registrations/{second}/paiement", headers=fin,
                         json={"decision": "verifie", "transaction_id": "TX-PARTAGE", "motif": "Essai"})
    assert response.status_code == 409


# ---------------------------------------------------------------------------
# B8 / B10 : matricules et capacité sous concurrence
# ---------------------------------------------------------------------------

def test_concurrent_registrations_respect_capacity_and_unique_matricules(api, seed_dortoir, mongo_db):
    seed_dortoir(capacity=3)
    bodies = [registration_body(f"TX-CONC-{i}") for i in range(8)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        statuses = list(pool.map(lambda b: api.post(f"{API}/registrations", json=b).status_code, bodies))

    assert statuses.count(201) == 3 and statuses.count(409) == 5
    assert mongo_db.dortoirs.find_one({"code": "DTEST"})["current_count"] == 3
    matricules = [r["matricule"] for r in mongo_db.registrations.find()]
    assert len(set(matricules)) == 3
    prefix = matricule_prefix(datetime.now(timezone.utc))
    assert all(m.startswith(f"{prefix}-DTEST-") for m in matricules)


# ---------------------------------------------------------------------------
# B5 / B6 / B7 : bulletins
# ---------------------------------------------------------------------------

def give_notes(api, headers, matricule, entree, evaluations, niveau="Niveau 1"):
    assert api.post(f"{API}/scientific/test-entree", params={"matricule": matricule, "note": entree, "niveau": niveau},
                    headers=headers).status_code == 201
    for i, value in enumerate(evaluations):
        assert api.post(f"{API}/scientific/notes", params={"matricule": matricule, "note": value, "libelle": f"Eval {i + 1}"},
                        headers=headers).status_code == 201


def test_bulletins_ranks_with_ties_and_incomplete_students(api, seed_dortoir, seed_user, login):
    seed_dortoir(capacity=10)
    sci = login(seed_user("sci.integ", "scientifique"))
    matricules = [api.post(f"{API}/registrations", json=registration_body(f"TX-BUL-{i}")).json()["matricule"] for i in range(4)]
    give_notes(api, sci, matricules[0], 18, [18, 18, 18])
    give_notes(api, sci, matricules[1], 18, [18, 18, 18])
    give_notes(api, sci, matricules[2], 12, [14, 16, 18])   # moyenne 15
    give_notes(api, sci, matricules[3], 20, [20])           # incomplet

    def generate(matricule):
        return api.post(f"{API}/scientific/bulletins/generate",
                        json={"matricule": matricule, "annee_scolaire": "2026"}, headers=sci)

    first, second, third = (generate(m).json() for m in matricules[:3])
    assert (first["rang"], second["rang"], third["rang"]) == (1, 1, 3)
    assert first["effectif_classe"] == 3 and third["moyenne_generale"] == 15.0 and third["mention"] == "Très bien"

    incomplete = generate(matricules[3])
    assert incomplete.status_code == 422
    assert "évaluation 2" in incomplete.json()["detail"] and "évaluation 3" in incomplete.json()["detail"]

    # B5 : le détail du bulletin ne provoque plus d'erreur 500
    detail = api.get(f"{API}/scientific/bulletins/{first['numero']}", headers=sci)
    assert detail.status_code == 200, detail.text
    assert len(detail.json()["notes"]) == 4


# ---------------------------------------------------------------------------
# B12 / B13 : export Excel et listes bornées
# ---------------------------------------------------------------------------

def test_excel_export(api, seed_dortoir, seed_user, login):
    seed_dortoir(capacity=10)
    admin = login(seed_user("admin.integ", "admin"))
    api.post(f"{API}/registrations", json=registration_body("TX-XLS-1", nom="=HYPERLINK(x)"))

    response = api.get(f"{API}/admin/export/seminaristes.xlsx", headers=admin)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/vnd.openxmlformats")
    sheet = load_workbook(io.BytesIO(response.content)).active
    rows = list(sheet.values)
    assert rows[0][0] == "Matricule" and "Allergies" not in rows[0] and len(rows) == 2
    assert rows[1][1] == "'=HYPERLINK(X)"  # injection de formule neutralisée
    assert rows[1][11] == "en_attente"


def test_lists_are_bounded_with_total_header(api, seed_user, login):
    admin = login(seed_user("admin.integ", "admin"))
    for i in range(3):
        assert api.post(f"{API}/visiteurs", json={"nom": f"Visiteur{i}", "prenom": "Fictif", "contact": "0100000000"}).status_code == 201

    page = api.get(f"{API}/visiteurs", params={"limit": 2}, headers=admin)
    assert page.headers["x-total-count"] == "3"
    assert page.json()["total"] == 3 and len(page.json()["data"]) == 2
    assert len(api.get(f"{API}/visiteurs", params={"skip": 2}, headers=admin).json()["data"]) == 1
    assert api.get(f"{API}/visiteurs", params={"limit": 5000}, headers=admin).status_code == 422

    users = api.get(f"{API}/admin/users", params={"limit": 1}, headers=admin)
    assert users.headers["x-total-count"] == "1" and isinstance(users.json(), list)


# ---------------------------------------------------------------------------
# Statistiques santé
# ---------------------------------------------------------------------------

def test_sante_statistics_are_aggregated(api, seed_dortoir, seed_user, login, mongo_db):
    seed_dortoir(capacity=10, gender="M")
    seed_dortoir(code="DFEM", capacity=10, gender="F")
    sante = login(seed_user("sante.integ", "sante"))
    homme = api.post(f"{API}/registrations", json=registration_body("TX-ST-1")).json()["matricule"]
    femme_body = registration_body("TX-ST-2", dortoir="DFEM")
    femme_body["personalInfo"]["sexe"] = "F"
    femme_body["healthInfo"]["allergie"] = "Arachide (fictif)"
    femme = api.post(f"{API}/registrations", json=femme_body).json()["matricule"]

    body = {"date_consultation": datetime.now(timezone.utc).isoformat(), "motif_consultation": "Motif fictif",
            "observations_diagnostic": "Diagnostic fictif", "traitement_prescrit": "Traitement fictif"}
    for matricule in (homme, homme, femme):
        assert api.post(f"{API}/sante/patients/{matricule}/consultations", json=body, headers=sante).status_code == 201

    stats = api.get(f"{API}/sante/statistiques", headers=sante).json()
    assert stats["total_seminaristes"] == 2 and stats["seminaristes_par_sexe"] == {"M": 1, "F": 1}
    assert stats["total_consultations"] == 3 and stats["consultations_aujourdhui"] == 3
    assert stats["patients_consultes"] == 2 and stats["consultations_par_sexe"] == {"M": 2, "F": 1}
    assert stats["patients_avec_allergies"] == 1 and stats["patients_avec_antecedents"] == 0
    assert len(stats["consultations_7_derniers_jours"]) == 7
    assert stats["consultations_7_derniers_jours"][-1]["nombre"] == 3
    assert "Motif fictif" not in str(stats)
