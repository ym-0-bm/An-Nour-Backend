import io
from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from prisma.errors import UniqueViolationError

from app.config import settings
from app.database import prisma
from app.main import app
from app.routes import registrations as registrations_module
from app.utils import finance_utils
from app.utils.rate_limit import RateLimiter, client_ip
from tests.test_auth import IDS, FakeUserActions, auth

API = "/api/v1"
SECRET_MARKER = "detail-interne-confidentiel-42"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(prisma, "user", FakeUserActions())
    return TestClient(app, raise_server_exceptions=False)


def png_bytes(size=(20, 20)):
    buffer = io.BytesIO()
    Image.new("RGB", size, "white").save(buffer, format="PNG")
    return buffer.getvalue()


class Raising:
    """Faux modèle Prisma dont toutes les méthodes lèvent l'exception donnée."""

    def __init__(self, exc):
        self.exc = exc

    def __getattr__(self, name):
        async def method(*args, **kwargs):
            raise self.exc
        return method


# ---------------------------------------------------------------------------
# Erreurs homogènes, sans détail interne
# ---------------------------------------------------------------------------

def test_unhandled_error_is_generic_and_traceable(client, monkeypatch):
    monkeypatch.setattr(prisma, "dortoir", Raising(RuntimeError(SECRET_MARKER)))
    response = client.get(f"{API}/registrations/dortoirs")
    assert response.status_code == 500
    body = response.json()
    assert body["detail"] == "Erreur interne du serveur"
    assert SECRET_MARKER not in response.text
    assert body["request_id"] == response.headers["x-request-id"]


def test_unique_violation_returns_409(client, monkeypatch):
    error = UniqueViolationError({"user_facing_error": {"message": SECRET_MARKER}})
    monkeypatch.setattr(prisma, "dortoir", Raising(error))
    response = client.get(f"{API}/registrations/dortoirs")
    assert response.status_code == 409
    assert SECRET_MARKER not in response.text


def test_update_user_conflict_returns_409(client):
    fake = prisma.user
    # Domaine fictif non réservé (email-validator refuse les domaines spéciaux comme .test)
    taken = fake.users[IDS["other_admin"]].model_copy(update={"email": "deja.pris@an-nour-fictif.org"})
    fake.users[IDS["other_admin"]] = taken

    async def find_unique(where):
        if "id" in where:
            return fake.users.get(where["id"])
        return next((u for u in fake.users.values() if all(getattr(u, k) == v for k, v in where.items())), None)

    fake.find_unique = find_unique
    for payload in ({"email": taken.email}, {"username": taken.username}):
        response = client.put(f"{API}/admin/users/{IDS['scientifique']}", json=payload, headers=auth("admin"))
        assert response.status_code == 409
    assert fake.updates == []


def test_finance_helper_hides_internal_error(monkeypatch):
    import asyncio
    from datetime import datetime

    monkeypatch.setattr(prisma, "transaction", Raising(RuntimeError(SECRET_MARKER)))
    result = asyncio.run(finance_utils.create_inscription_entry("M-TEST", "NOM", "PRENOM", 6000.0, datetime.now()))
    assert result["success"] is False
    assert SECRET_MARKER not in str(result)


# ---------------------------------------------------------------------------
# Vérification des reçus et téléversements
# ---------------------------------------------------------------------------

@pytest.fixture
def fake_ocr(monkeypatch):
    calls = []
    monkeypatch.setattr(registrations_module.ocr_processor, "process_image", lambda content: "texte-ocr-fictif")

    async def analyze_receipt(text, expected_amount):
        calls.append(expected_amount)
        return {"valid": True}

    monkeypatch.setattr(registrations_module.receipt_analyzer, "analyze_receipt", analyze_receipt)
    return calls


def post_receipt(client, content, content_type="image/png", query=""):
    return client.post(f"{API}/registrations/verify-receipt{query}", files={"file": ("recu.png", content, content_type)})


def test_receipt_amount_is_fixed_by_server(client, fake_ocr):
    response = post_receipt(client, png_bytes(), query="?expected_amount=100")
    assert response.status_code == 200
    assert fake_ocr == [settings.REGISTRATION_FEE] == [6000]


@pytest.mark.parametrize("content,content_type", [
    (b"ceci n'est pas une image", "image/png"),
    (b"", "image/png"),
    (b"GIF89a" + b"\x00" * 50, "image/gif"),
])
def test_receipt_rejects_non_images(client, fake_ocr, content, content_type):
    assert post_receipt(client, content, content_type).status_code == 400
    assert fake_ocr == []


def test_receipt_rejects_unsupported_format(client, fake_ocr):
    buffer = io.BytesIO()
    Image.new("RGB", (10, 10)).save(buffer, format="GIF")
    assert post_receipt(client, buffer.getvalue(), "image/gif").status_code == 400


def test_receipt_rejects_oversized_file(client, fake_ocr):
    oversized = png_bytes() + b"\x00" * settings.MAX_UPLOAD_BYTES
    response = post_receipt(client, oversized)
    assert response.status_code == 413
    assert fake_ocr == []


def test_request_body_over_limit_is_rejected_early(client, fake_ocr):
    huge = b"\x00" * (settings.MAX_UPLOAD_BYTES + 2 * 1024 * 1024)
    response = post_receipt(client, huge)
    assert response.status_code == 413
    assert response.json()["detail"] == "Requête trop volumineuse"


def test_receipt_ocr_failure_is_generic(client, monkeypatch, fake_ocr):
    def boom(content):
        raise RuntimeError(SECRET_MARKER)

    monkeypatch.setattr(registrations_module.ocr_processor, "process_image", boom)
    response = post_receipt(client, png_bytes())
    assert response.status_code == 500
    assert SECRET_MARKER not in response.text


@pytest.fixture
def fake_photo_upload(monkeypatch):
    registration = SimpleNamespace(matricule="M-TEST")

    async def find_unique(where):
        return registration

    async def update(where, data):
        return registration

    monkeypatch.setattr(prisma, "registration", SimpleNamespace(find_unique=find_unique, update=update))
    uploads = []
    monkeypatch.setattr(registrations_module.cloudinary.uploader, "upload", lambda *a, **k: uploads.append(a) or {"secure_url": "https://exemple.test/p.png"})
    return uploads


def test_photo_upload_validates_content(client, fake_photo_upload):
    response = client.post(f"{API}/registrations/M-TEST/photo", files={"photo": ("p.png", b"pas une image", "image/png")}, headers=auth("admin"))
    assert response.status_code == 400
    assert fake_photo_upload == []

    response = client.post(f"{API}/registrations/M-TEST/photo", files={"photo": ("p.png", png_bytes(), "image/png")}, headers=auth("admin"))
    assert response.status_code == 200
    assert len(fake_photo_upload) == 1


def test_photo_storage_error_is_generic(client, fake_photo_upload, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError(SECRET_MARKER)

    monkeypatch.setattr(registrations_module.cloudinary.uploader, "upload", boom)
    response = client.post(f"{API}/registrations/M-TEST/photo", files={"photo": ("p.png", png_bytes(), "image/png")}, headers=auth("admin"))
    assert response.status_code == 502
    assert SECRET_MARKER not in response.text


@pytest.fixture
def fake_registration_db(monkeypatch):
    """Faux Prisma pour la création d'inscription : enregistre les appels."""
    state = {"created": [], "dortoir_updates": [], "finance_calls": [], "reserved": 1, "create_errors": 0}
    dortoir = SimpleNamespace(code="D1", current_count=0, capacity=10, gender="M", name="Dortoir 1")

    async def dortoir_find_unique(where):
        return dortoir

    async def dortoir_update_many(where, data):
        return state["reserved"]

    async def dortoir_update(where, data):
        state["dortoir_updates"].append(data)
        return dortoir

    async def none(*args, **kwargs):
        return None

    async def create(data):
        if state["create_errors"]:
            state["create_errors"] -= 1
            raise UniqueViolationError({"user_facing_error": {"message": "doublon"}})
        state["created"].append(data)
        return SimpleNamespace(id="r" * 24)

    async def find_unique(where, include=None):
        data = state["created"][-1]
        return SimpleNamespace(id="r" * 24, dortoir=dortoir, photo_url=None, validated=False,
                               registration_date=datetime(2026, 1, 1), **{
                                   k: data.get(k) for k in ("matricule", "nom", "prenom", "sexe", "age",
                                                            "commune_habitation", "contact_parent", "contact_seminariste",
                                                            "niveau_academique", "dortoir_code", "allergie",
                                                            "antecedent_medical", "payment_status", "transaction_id")})

    async def matricule(code):
        return f"ANNOUR26-{code}-{len(state['created']) + 1:03d}"

    async def finance(**kwargs):
        state["finance_calls"].append(kwargs)

    monkeypatch.setattr(prisma, "dortoir", SimpleNamespace(find_unique=dortoir_find_unique,
                                                           update_many=dortoir_update_many, update=dortoir_update))
    monkeypatch.setattr(prisma, "usedtransaction", SimpleNamespace(find_unique=none))
    monkeypatch.setattr(prisma, "registration", SimpleNamespace(find_first=none, create=create, find_unique=find_unique))
    monkeypatch.setattr(registrations_module, "generate_matricule", matricule)
    monkeypatch.setattr(registrations_module, "create_inscription_entry", finance)
    return state


def test_registration_is_pending_without_finance_entry(client, fake_registration_db):
    response = client.post(f"{API}/registrations", json=registration_body(amount=1))
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["payment_status"] == "en_attente"
    token = body["verification_token"]
    assert len(token) >= 32

    stored = fake_registration_db["created"][0]
    assert stored["payment_status"] == "en_attente"
    assert stored["receipt_token_hash"] != token  # le jeton n'est jamais stocké en clair
    assert fake_registration_db["finance_calls"] == []  # aucune entrée financière avant vérification


def test_full_dormitory_creates_nothing(client, fake_registration_db):
    fake_registration_db["reserved"] = 0  # réservation atomique refusée
    assert client.post(f"{API}/registrations", json=registration_body()).status_code == 409
    assert fake_registration_db["created"] == []


def test_failed_creation_releases_reserved_place(client, fake_registration_db):
    fake_registration_db["create_errors"] = 99  # collisions de matricule répétées
    assert client.post(f"{API}/registrations", json=registration_body()).status_code == 503
    assert fake_registration_db["dortoir_updates"] == [{"current_count": {"decrement": 1}}]


def test_matricule_collision_is_retried(client, fake_registration_db):
    fake_registration_db["create_errors"] = 1
    assert client.post(f"{API}/registrations", json=registration_body()).status_code == 201
    assert fake_registration_db["dortoir_updates"] == []


# ---------------------------------------------------------------------------
# Limitation de débit
# ---------------------------------------------------------------------------

def test_rate_limiter_sliding_window():
    now = [1000.0]
    limiter = RateLimiter(clock=lambda: now[0])
    assert [limiter.hit("k", 3, 60) for _ in range(3)] == [None, None, None]
    assert limiter.hit("k", 3, 60) == 60
    now[0] += 30
    assert limiter.hit("k", 3, 60) == 30
    now[0] += 30.5
    assert limiter.hit("k", 3, 60) is None
    assert limiter.hit("autre", 3, 60) is None


def test_client_ip_uses_proxy_appended_entry():
    def request(headers, host="10.0.0.1"):
        return SimpleNamespace(headers=headers, client=SimpleNamespace(host=host))

    assert client_ip(request({})) == "10.0.0.1"
    assert client_ip(request({"x-forwarded-for": "203.0.113.7"})) == "203.0.113.7"
    # La première entrée est contrôlée par le client : seule la dernière (ajoutée par le proxy) compte
    assert client_ip(request({"x-forwarded-for": "1.2.3.4, 203.0.113.7"})) == "203.0.113.7"


@pytest.mark.parametrize("method,path,limit", [
    ("POST", f"{API}/feedback", 30),
    ("POST", f"{API}/visiteurs", 60),
    ("POST", f"{API}/registrations", 20),
    ("POST", f"{API}/admin/auth/login", 10),
])
def test_public_routes_are_rate_limited(client, method, path, limit):
    # Corps vide : chaque appel compte même s'il est invalide (422)
    statuses = [client.request(method, path, json={}).status_code for _ in range(limit)]
    assert 429 not in statuses
    response = client.request(method, path, json={})
    assert response.status_code == 429
    assert int(response.headers["retry-after"]) >= 1

    # Une autre IP n'est pas affectée
    other = client.request(method, path, json={}, headers={"X-Forwarded-For": "198.51.100.9"})
    assert other.status_code != 429


def test_spoofed_forwarded_prefix_does_not_bypass_limit(client):
    for i in range(30):
        client.post(f"{API}/feedback", json={}, headers={"X-Forwarded-For": f"10.0.{i}.1, 203.0.113.7"})
    response = client.post(f"{API}/feedback", json={}, headers={"X-Forwarded-For": "10.9.9.9, 203.0.113.7"})
    assert response.status_code == 429


def test_receipt_rate_limit(client, fake_ocr):
    for _ in range(10):
        assert post_receipt(client, png_bytes()).status_code == 200
    assert post_receipt(client, png_bytes()).status_code == 429


def login(client, identifier, password, ip):
    return client.post(f"{API}/admin/auth/login", json={"identifier": identifier, "password": password},
                       headers={"X-Forwarded-For": ip})


def test_login_locks_identifier_after_failures_across_ips(client):
    email = prisma.user.users[IDS["admin"]].email
    for i in range(5):
        assert login(client, email, "mauvais-mdp", f"203.0.113.{i}").status_code == 401
    # Même le bon mot de passe, depuis une nouvelle IP, est refusé pendant la fenêtre
    response = login(client, email, "motdepasse-fictif", "198.51.100.1")
    assert response.status_code == 429
    # Les autres comptes ne sont pas bloqués
    other = prisma.user.users[IDS["scientifique"]].email
    assert login(client, other, "motdepasse-fictif", "198.51.100.1").status_code == 200


def test_login_success_resets_failures(client):
    email = prisma.user.users[IDS["admin"]].email
    for _ in range(4):
        login(client, email, "mauvais-mdp", "203.0.113.1")
    assert login(client, email, "motdepasse-fictif", "203.0.113.1").status_code == 200
    for _ in range(4):
        assert login(client, email, "mauvais-mdp", "203.0.113.2").status_code == 401


def test_unknown_identifier_is_also_limited(client):
    for _ in range(5):
        assert login(client, "inconnu@exemple.test", "x", "203.0.113.1").status_code == 401
    assert login(client, "INCONNU@exemple.test ", "x", "203.0.113.2").status_code == 429


# ---------------------------------------------------------------------------
# Validation des entrées publiques
# ---------------------------------------------------------------------------

def registration_body(amount=6000, **personal):
    info = {
        "nom": "Fictif", "prenom": "Test", "sexe": "M", "age": "20",
        "communeHabitation": "Commune", "niveauAcademique": "Licence",
        "contactParent": "0102030405",
    }
    info.update(personal)
    return {
        "personalInfo": info,
        "dormitoryInfo": {"dortoir": "Dortoir 1", "dortoirId": "D1"},
        "healthInfo": {},
        "paymentInfo": {"transactionId": "TX-FICTIF-1", "amount": amount},
    }


@pytest.mark.parametrize("path,body", [
    (f"{API}/visiteurs", {"nom": "x" * 101, "prenom": "P", "contact": "0102030405"}),
    (f"{API}/visiteurs", {"nom": "   ", "prenom": "P", "contact": "0102030405"}),
    (f"{API}/visiteurs", {"nom": "N", "prenom": "P", "contact": "0" * 31}),
    (f"{API}/feedback", {"note_globale": 5, "qualite_nourriture": 5, "confort_dortoirs": 5,
                         "qualite_formations": 5, "qualite_contenu": 5, "note_organisation": 8,
                         "duree_appropriee": True, "recommande": True, "suggestions": "x" * 2001}),
    (f"{API}/registrations", registration_body(nom="x" * 101)),
    (f"{API}/registrations", registration_body(age="1000")),
    (f"{API}/admin/auth/login", {"identifier": "a", "password": "x" * 129}),
    (f"{API}/admin/auth/login", {"identifier": "", "password": "x"}),
])
def test_public_inputs_are_bounded(client, path, body):
    assert client.post(path, json=body).status_code == 422


def test_whitespace_is_trimmed(client, monkeypatch):
    stored = {}

    async def create(data):
        stored.update(data)
        return SimpleNamespace(id="v" * 24, created_at="2026-01-01T00:00:00", **data)

    monkeypatch.setattr(prisma, "visiteur", SimpleNamespace(create=create))
    client.post(f"{API}/visiteurs", json={"nom": "  Fictif ", "prenom": " Test", "contact": " 0102030405 "})
    # La route met le nom en majuscules ; on vérifie seulement que les espaces ont été retirés
    assert stored["nom"].lower() == "fictif" and stored["contact"] == "0102030405"


# ---------------------------------------------------------------------------
# En-têtes HTTP, CORS, fichiers statiques
# ---------------------------------------------------------------------------

def test_security_headers(client):
    response = client.get("/health")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["cache-control"] == "no-store"
    assert len(response.headers["x-request-id"]) == 32
    assert "strict-transport-security" not in response.headers  # uniquement en production


def test_docs_remain_available(client):
    assert client.get("/docs").status_code == 200
    assert client.get("/openapi.json").status_code == 200


def test_cors_allows_known_origin_only(client):
    preflight = {"Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "authorization,content-type"}
    allowed = client.options(f"{API}/admin/auth/login", headers={"Origin": "https://an-nour25.vercel.app", **preflight})
    assert allowed.status_code == 200
    assert allowed.headers["access-control-allow-origin"] == "https://an-nour25.vercel.app"

    unknown = client.options(f"{API}/admin/auth/login", headers={"Origin": "https://malveillant.example", **preflight})
    assert "access-control-allow-origin" not in unknown.headers

    odd_header = client.options(f"{API}/admin/auth/login", headers={
        "Origin": "https://an-nour25.vercel.app", "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "x-inattendu"})
    assert odd_header.status_code == 400


def test_media_directory_is_not_served(client):
    assert client.get("/media/participants_photos/.gitkeep").status_code == 404


# ---------------------------------------------------------------------------
# B16 / B17 : santé du service et journalisation
# ---------------------------------------------------------------------------

def test_health_checks_the_database(client):
    # Sans base connectée (tests unitaires) : 503, sans détail interne
    response = client.get("/health")
    assert response.status_code == 503
    assert response.json() == {"status": "unhealthy", "database": "unavailable", "version": app.version}


def test_liveness_does_not_need_the_database(client):
    assert client.get("/health/live").json() == {"status": "alive"}


def test_access_log_never_contains_query_string(client, caplog):
    import logging
    with caplog.at_level(logging.INFO, logger="app.access"):
        response = client.get(f"{API}/visiteurs", params={"search": "NomConfidentiel"})
    access = [r for r in caplog.records if r.name == "app.access"]
    assert access and all("NomConfidentiel" not in r.getMessage() and "?" not in r.getMessage() for r in access)
    assert access[-1].request_id == response.headers["x-request-id"]


# ---------------------------------------------------------------------------
# T3 : source de l'IP client derrière le proxy
# ---------------------------------------------------------------------------

def fake_request(headers, host="10.0.0.1"):
    return SimpleNamespace(headers=headers, client=SimpleNamespace(host=host))


@pytest.mark.parametrize("source,expected", [
    ("forwarded_last", "203.0.113.7"),
    ("forwarded_first", "1.2.3.4"),
    ("cf-connecting-ip", "198.51.100.5"),
    ("true-client-ip", "198.51.100.6"),
    ("direct", "10.0.0.1"),
])
def test_client_ip_source_is_configurable(monkeypatch, source, expected):
    from app.utils import rate_limit
    monkeypatch.setattr(rate_limit.settings, "CLIENT_IP_SOURCE", source)
    headers = {"x-forwarded-for": "1.2.3.4, 203.0.113.7", "cf-connecting-ip": "198.51.100.5",
               "true-client-ip": "198.51.100.6"}
    assert rate_limit.client_ip(fake_request(headers)) == expected


def test_missing_configured_header_falls_back_to_direct_peer(monkeypatch):
    from app.utils import rate_limit
    monkeypatch.setattr(rate_limit.settings, "CLIENT_IP_SOURCE", "cf-connecting-ip")
    assert rate_limit.client_ip(fake_request({})) == "10.0.0.1"


def test_client_ip_diagnostic_for_admin(client):
    from tests.test_auth import auth
    response = client.get("/api/v1/admin/diagnostic/client-ip", headers={**auth("admin"),
                          "X-Forwarded-For": "1.2.3.4, 203.0.113.7", "CF-Connecting-IP": "198.51.100.5"})
    assert response.status_code == 200
    body = response.json()
    assert body["source_actuelle"] == "forwarded_last" and body["ip_retenue"] == "203.0.113.7"
    assert body["candidats"]["forwarded_first"] == "1.2.3.4"
    assert body["candidats"]["cf-connecting-ip"] == "198.51.100.5"
