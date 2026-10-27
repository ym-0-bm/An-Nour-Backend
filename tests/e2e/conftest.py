"""
Tests de bout en bout (Partie 11 / T1) : l'API tourne dans son image Docker de production, les parcours
sont joués uniquement par HTTP, comme la PWA et l'interface d'administration.

Activés si E2E_BASE_URL (ex. http://127.0.0.1:8000) et E2E_DATABASE_URL sont définis. Garde-fou :
API et base locales, nom de base contenant "test" ; sinon la session s'arrête avant toute écriture.
"""
import os
import time
from datetime import datetime, timezone
from urllib.parse import urlparse

import httpx
import pytest

BASE_URL = os.environ.get("E2E_BASE_URL")
DB_URL = os.environ.get("E2E_DATABASE_URL")
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
PASSWORD = "motdepasse-fictif-e2e"
ROLES = ("admin", "finances", "scientifique", "sante")


def _check_safe_targets():
    db = urlparse(DB_URL)
    api = urlparse(BASE_URL)
    db_name = db.path.lstrip("/")
    if db.hostname not in LOCAL_HOSTS or "test" not in db_name.lower() or api.hostname not in LOCAL_HOSTS:
        pytest.exit("E2E : API et base doivent être locales, et le nom de la base contenir 'test'.", returncode=2)
    return db_name


if BASE_URL and DB_URL:
    DB_NAME = _check_safe_targets()


def pytest_collection_modifyitems(config, items):
    for item in items:
        if "e2e" in os.path.normpath(str(item.fspath)).split(os.sep):
            item.add_marker(pytest.mark.e2e)
            if not (BASE_URL and DB_URL):
                item.add_marker(pytest.mark.skip(reason="E2E_BASE_URL / E2E_DATABASE_URL non définis"))


@pytest.fixture(scope="session")
def mongo():
    from pymongo import MongoClient
    from app.utils.auth import get_password_hash

    client = MongoClient(DB_URL, serverSelectionTimeoutMS=10000)
    db = client[DB_NAME]
    assert "test" in db.name.lower()
    for name in db.list_collection_names():
        db[name].delete_many({})

    now = datetime.now(timezone.utc)
    db.dortoirs.insert_many([
        {"code": "E2EM", "name": "Dortoir E2E M", "capacity": 50, "current_count": 0, "gender": "M",
         "createdAt": now, "updatedAt": now},
        {"code": "E2EF", "name": "Dortoir E2E F", "capacity": 50, "current_count": 0, "gender": "F",
         "createdAt": now, "updatedAt": now},
    ])
    password_hash = get_password_hash(PASSWORD)
    db.users.insert_many([{
        "email": f"{role}.e2e@an-nour-fictif.org", "username": f"{role}.e2e", "password": password_hash,
        "nom": "E2E", "prenom": role.capitalize(), "role": role, "is_active": True,
        "created_at": now, "updated_at": now,
    } for role in ROLES])
    yield db
    client.close()


@pytest.fixture(scope="session")
def api():
    with httpx.Client(base_url=BASE_URL, timeout=120) as client:
        deadline = time.monotonic() + 180
        while True:
            try:
                if client.get("/health").status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            if time.monotonic() > deadline:
                pytest.exit("E2E : l'API ne répond pas sur /health.", returncode=2)
            time.sleep(2)
        yield client


@pytest.fixture(scope="session")
def tokens(api, mongo):
    """Un token par rôle (une connexion par rôle : la limite de débit du login reste respectée)."""
    result = {}
    for role in ROLES:
        response = api.post("/api/v1/admin/auth/login", json={"identifier": f"{role}.e2e", "password": PASSWORD})
        assert response.status_code == 200, response.text
        result[role] = {"Authorization": f"Bearer {response.json()['access_token']}"}
    return result
