"""Parcours de bout en bout, joués par HTTP contre l'API dans son image Docker (OCR réel compris)."""
import io
import uuid
from datetime import datetime, timedelta, timezone

from openpyxl import load_workbook
from PIL import Image, ImageDraw, ImageFont

API = "/api/v1"
FEE = 6000


def unique_tx():
    return f"TX-E2E-{uuid.uuid4().hex[:10].upper()}"


def registration(nom="Parcours", sexe="M", tx=None):
    return {
        "personalInfo": {"nom": nom, "prenom": "Fictif", "sexe": sexe, "age": "19",
                         "communeHabitation": "Commune fictive", "niveauAcademique": "Licence",
                         "contactParent": "0100000000", "dateNaissance": "2007-03-15"},
        "dormitoryInfo": {"dortoir": "Dortoir E2E", "dortoirId": "E2EM" if sexe == "M" else "E2EF"},
        "healthInfo": {"allergie": "RAS", "antecedentMedical": "Néant"},
        "paymentInfo": {"transactionId": tx or unique_tx(), "amount": FEE},
    }


def register(api, **kwargs):
    response = api.post(f"{API}/registrations", json=registration(**kwargs))
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["payment_status"] == "en_attente" and body["verification_token"]
    return body["matricule"], body["verification_token"]


def receipt_png(transaction_id):
    """Capture fictive de reçu Wave, lue par le vrai Tesseract du conteneur."""
    lines = ["-6.000F", "Paiement AN-NOUR", "Statut Effectue", "En partenariat avec UBA",
             "ID de transaction", transaction_id]
    image = Image.new("RGB", (900, 80 * len(lines) + 40), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=44)
    for i, line in enumerate(lines):
        draw.text((30, 20 + i * 80), line, fill="black", font=font)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def submit_receipt(api, matricule, token, content):
    return api.post(f"{API}/registrations/{matricule}/receipt", data={"token": token},
                    files={"file": ("recu.png", content, "image/png")})


def test_inscription_puis_paiement_verifie_par_ocr(api, tokens):
    # ID au format Wave, sans caractères ambigus pour l'OCR (base vidée à chaque session : pas de collision)
    tx = "TABCDEFGHJKLMNPQR"
    matricule, token = register(api)

    # Le paiement n'entre pas en finances avant vérification
    before = api.get(f"{API}/finances/dashboard", headers=tokens["finances"]).json()["inscriptions"]

    result = submit_receipt(api, matricule, token, receipt_png(tx))
    assert result.status_code == 200, result.text
    assert result.json()["statut"] == "verifie", result.json()

    paiement = api.get(f"{API}/registrations/{matricule}/paiement", headers=tokens["finances"]).json()
    assert (paiement["statut"], paiement["methode"], paiement["transaction_id"]) == ("verifie", "ocr", tx)

    after = api.get(f"{API}/finances/dashboard", headers=tokens["finances"]).json()["inscriptions"]
    assert after["count"] == before["count"] + 1 and after["montant"] == before["montant"] + FEE

    # Le même reçu ne valide pas une seconde inscription ; un second envoi est refusé
    other, other_token = register(api, nom="Autre")
    assert submit_receipt(api, other, other_token, receipt_png(tx)).json()["statut"] == "rejete"
    assert submit_receipt(api, matricule, token, receipt_png(tx)).status_code == 409


def test_recu_illisible_puis_validation_manuelle(api, tokens):
    matricule, token = register(api)
    blank = io.BytesIO()
    Image.new("RGB", (400, 200), "white").save(blank, format="PNG")
    assert submit_receipt(api, matricule, token, blank.getvalue()).json()["statut"] in ("erreur", "rejete")

    tx = unique_tx()
    decision = api.patch(f"{API}/registrations/{matricule}/paiement", headers=tokens["finances"],
                         json={"decision": "verifie", "transaction_id": tx, "motif": "Relevé Wave contrôlé (E2E)"})
    assert decision.status_code == 200 and decision.json()["statut"] == "verifie"
    paiement = api.get(f"{API}/registrations/{matricule}/paiement", headers=tokens["finances"]).json()
    assert paiement["methode"] == "manuel" and paiement["verified_by"] == "finances.e2e"
    assert [h["statut"] for h in paiement["historique"]][0] == "verifie"


def test_notes_bulletins_rangs_et_recalcul(api, tokens):
    sci = tokens["scientifique"]
    annee = f"E2E-{uuid.uuid4().hex[:6]}"
    eleves = {}
    for nom, notes in (("Premier", (16, 16, 16, 16)), ("Second", (16, 16, 16, 16)), ("Troisieme", (10, 10, 10, 10))):
        matricule, _ = register(api, nom=nom)
        entree, *evaluations = notes
        assert api.post(f"{API}/scientific/test-entree", headers=sci,
                        params={"matricule": matricule, "note": entree, "niveau": annee}).status_code == 201
        for i, value in enumerate(evaluations, start=1):
            assert api.post(f"{API}/scientific/notes", headers=sci,
                            params={"matricule": matricule, "note": value, "libelle": f"Eval {i}"}).status_code == 201
        eleves[nom] = matricule

    numeros = {}
    for nom in ("Troisieme", "Premier", "Second"):  # ordre volontairement mélangé
        response = api.post(f"{API}/scientific/bulletins/generate", headers=sci,
                            json={"matricule": eleves[nom], "annee_scolaire": annee})
        assert response.status_code == 201, response.text
        numeros[nom] = response.json()["numero"]

    rangs = {nom: api.get(f"{API}/scientific/bulletins/{numeros[nom]}", headers=sci).json()["bulletin"]["rang"]
             for nom in numeros}
    assert rangs == {"Premier": 1, "Second": 1, "Troisieme": 3}  # ex aequo puis saut, rangs à jour partout

    # Correction d'une note puis recalcul de l'année
    notes = api.get(f"{API}/scientific/seminaristes/{eleves['Troisieme']}/notes", headers=sci).json()
    evaluation = next(n for n in notes if n["type"] == "EVALUATION")
    assert api.put(f"{API}/scientific/notes/{evaluation['id']}", headers=sci, json={"note": 20}).status_code == 200
    recalcul = api.post(f"{API}/scientific/bulletins/recalculer", headers=sci, params={"annee_scolaire": annee})
    assert recalcul.status_code == 200 and recalcul.json()["moyennes_modifiees"] == 1

    # Notes incomplètes (test d'entrée seul) : bulletin refusé, avec la liste des notes manquantes
    incomplet, _ = register(api, nom="Incomplet")
    assert api.post(f"{API}/scientific/test-entree", headers=sci,
                    params={"matricule": incomplet, "note": 12, "niveau": annee}).status_code == 201
    refus = api.post(f"{API}/scientific/bulletins/generate", headers=sci,
                     json={"matricule": incomplet, "annee_scolaire": annee})
    assert refus.status_code == 422 and "évaluation 1" in refus.json()["detail"]


def test_consultation_medicale_et_statistiques(api, tokens):
    sante = tokens["sante"]
    matricule, _ = register(api, nom="Patient", sexe="F")
    fiche = api.get(f"{API}/sante/patients/{matricule}/fiche-medicale", headers=sante).json()
    assert fiche["patient"]["date_naissance"] == "2007-03-15" and fiche["nombre_consultations"] == 0

    body = {"date_consultation": (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat(),
            "motif_consultation": "Motif fictif E2E", "observations_diagnostic": "Diagnostic fictif",
            "traitement_prescrit": "Traitement fictif"}
    created = api.post(f"{API}/sante/patients/{matricule}/consultations", headers=sante, json=body)
    assert created.status_code == 201, created.text
    export = api.get(f"{API}/sante/consultations/{created.json()['id']}/export", headers=sante).json()
    assert export["fiche"]["patient"]["matricule"] == matricule

    stats = api.get(f"{API}/sante/statistiques", headers=sante).json()
    assert stats["total_consultations"] >= 1 and stats["consultations_par_sexe"]["F"] >= 1
    assert api.get(f"{API}/sante/patients/{matricule}/fiche-medicale", headers=tokens["finances"]).status_code == 403


def test_administration_sessions_et_export(api, tokens):
    admin = tokens["admin"]
    login = f"agent.{uuid.uuid4().hex[:6]}"
    created = api.post(f"{API}/admin/auth/register", headers=admin, json={
        "email": f"{login}@an-nour-fictif.org", "username": login, "password": "motdepasse-agent-e2e",
        "nom": "Agent", "prenom": "Fictif", "role": "scientifique"})
    assert created.status_code == 201, created.text
    agent = api.post(f"{API}/admin/auth/login", json={"identifier": login, "password": "motdepasse-agent-e2e"})
    agent_headers = {"Authorization": f"Bearer {agent.json()['access_token']}"}
    assert api.get(f"{API}/admin/auth/me", headers=agent_headers).status_code == 200

    revoke = api.post(f"{API}/admin/users/{created.json()['id']}/revoke-sessions", headers=admin)
    assert revoke.status_code == 200
    assert api.get(f"{API}/admin/auth/me", headers=agent_headers).status_code == 401

    export = api.get(f"{API}/admin/export/seminaristes.xlsx", headers=admin)
    assert export.status_code == 200
    rows = list(load_workbook(io.BytesIO(export.content)).active.values)
    assert rows[0][0] == "Matricule" and len(rows) > 1


def test_securite_de_base(api):
    health = api.get("/health")
    assert health.status_code == 200 and health.json()["database"] == "ok"
    assert health.headers["x-content-type-options"] == "nosniff" and health.headers["x-frame-options"] == "DENY"
    assert api.get(f"{API}/admin/users").status_code == 401
    assert api.get(f"{API}/sante/statistiques").status_code == 401
    assert api.get(f"{API}/finances/dashboard", headers={"Authorization": "Bearer faux"}).status_code == 401
