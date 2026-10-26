"""
Tests d'intégration contre une vraie base MongoDB (replica set, schéma créé par `prisma db push`).

Activés uniquement si INTEGRATION_DATABASE_URL est défini. Garde-fou : la base doit être locale
et son nom doit contenir "test", sinon la session est interrompue avant toute écriture.
"""
from datetime import datetime, timezone

import pytest

# Le garde-fou et DATABASE_URL sont fixés dans tests/conftest.py, avant tout import de l'application
from tests.conftest import INTEGRATION_URL, check_safe_integration_target

if INTEGRATION_URL:
    DB_NAME = check_safe_integration_target(INTEGRATION_URL)


def pytest_collection_modifyitems(config, items):
    for item in items:
        if "integration" in str(item.fspath):
            item.add_marker(pytest.mark.integration)
            if not INTEGRATION_URL:
                item.add_marker(pytest.mark.skip(reason="INTEGRATION_DATABASE_URL non défini"))


@pytest.fixture(scope="session")
def mongo_db():
    from pymongo import MongoClient

    client = MongoClient(INTEGRATION_URL, serverSelectionTimeoutMS=5000)
    db = client[DB_NAME]
    assert "test" in db.name.lower()
    yield db
    client.close()


@pytest.fixture(autouse=True)
def clean_database(request):
    if "integration" not in str(request.node.fspath) or not INTEGRATION_URL:
        yield
        return
    db = request.getfixturevalue("mongo_db")
    for name in db.list_collection_names():
        db[name].delete_many({})
    yield


@pytest.fixture(scope="module")
def api(mongo_db):
    from fastapi.testclient import TestClient
    from app.main import app

    # Avec "with" : le lifespan connecte réellement Prisma à la base de test
    with TestClient(app, raise_server_exceptions=False) as client:
        _assert_api_uses_test_database(client, mongo_db)
        yield client


def _assert_api_uses_test_database(client, mongo_db):
    """Vérification de bout en bout : un témoin écrit dans la base de test doit être lu par l'API."""
    now = datetime.now(timezone.utc)
    sentinel = {"code": "__TEMOIN_INTEGRATION__", "name": "temoin", "capacity": 1, "current_count": 0,
                "gender": "M", "createdAt": now, "updatedAt": now}
    mongo_db.dortoirs.insert_one(sentinel)
    try:
        codes = {d["code"] for d in client.get("/api/v1/registrations/alldortoirs").json()}
    finally:
        mongo_db.dortoirs.delete_one({"code": "__TEMOIN_INTEGRATION__"})
    if "__TEMOIN_INTEGRATION__" not in codes:
        pytest.exit("L'API n'est pas connectée à la base de test : arrêt avant toute écriture.", returncode=2)


@pytest.fixture
def seed_user(mongo_db):
    from app.utils.auth import get_password_hash

    def create(username, role, password="motdepasse-fictif", is_active=True):
        now = datetime.now(timezone.utc)
        mongo_db.users.insert_one({
            "email": f"{username}@an-nour-fictif.org",
            "username": username,
            "password": get_password_hash(password),
            "nom": "Fictif",
            "prenom": username.capitalize(),
            "role": role,
            "is_active": is_active,
            "created_at": now,
            "updated_at": now,
        })
        return {"identifier": username, "password": password}

    return create


@pytest.fixture
def seed_dortoir(mongo_db):
    def create(code="DTEST", capacity=10, gender="M"):
        now = datetime.now(timezone.utc)
        mongo_db.dortoirs.insert_one({
            "code": code, "name": f"Dortoir {code}", "capacity": capacity,
            "current_count": 0, "gender": gender, "createdAt": now, "updatedAt": now,
        })
        return code

    return create


@pytest.fixture
def login(api):
    def do(credentials):
        response = api.post("/api/v1/admin/auth/login", json=credentials)
        assert response.status_code == 200, response.text
        return {"Authorization": f"Bearer {response.json()['access_token']}"}

    return do
