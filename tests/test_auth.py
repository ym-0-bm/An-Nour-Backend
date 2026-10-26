import re
import warnings
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
import jwt
from prisma.models import User

from app.database import prisma
from app.main import app
from app.utils.auth import (
    ALGORITHM, ROLES, SECRET_KEY, create_access_token,
    get_password_hash, normalize_role,
)

API = "/api/v1"
ADMIN, SCI, FIN, SANTE = "admin", "scientifique", "finances", "sante"

# Routes accessibles sans connexion (décision validée en Partie 2)
PUBLIC_ROUTES = {
    ("POST", f"{API}/admin/auth/login"),
    ("GET", f"{API}/admin/static-metadata"),
    ("POST", f"{API}/registrations"),
    ("POST", f"{API}/registrations/verify-receipt"),
    ("GET", f"{API}/registrations/dortoirs"),
    ("GET", f"{API}/registrations/alldortoirs"),
    ("POST", f"{API}/feedback"),
    ("POST", f"{API}/visiteurs"),
    # Envoi du reçu par l'inscrit : protégé par le jeton remis à l'inscription (Partie 8)
    ("POST", f"{API}/registrations/{{matricule}}/receipt"),
    ("GET", "/"),
    ("GET", "/health"),
}
ANY = "any"
LECTURE_SEMINARISTES = {ADMIN, SCI, SANTE}
SPECIFIC_RULES = {
    ("GET", f"{API}/admin/auth/me"): ANY,
    ("POST", f"{API}/admin/auth/logout"): ANY,
    ("GET", f"{API}/admin/metadata"): ANY,
    ("GET", f"{API}/admin/seminaristes/{{matricule}}"): LECTURE_SEMINARISTES,
    ("GET", f"{API}/registrations"): LECTURE_SEMINARISTES,
    ("GET", f"{API}/registrations/{{identifier}}"): LECTURE_SEMINARISTES,
    ("GET", f"{API}/registrations/stats/global"): LECTURE_SEMINARISTES,
    ("GET", f"{API}/scientific/seminaristes"): LECTURE_SEMINARISTES,
    ("GET", f"{API}/registrations/{{matricule}}/paiement"): {ADMIN, FIN},
    ("PATCH", f"{API}/registrations/{{matricule}}/paiement"): {ADMIN, FIN},
}


def expected_access(method, path):
    if (method, path) in PUBLIC_ROUTES:
        return None
    if (method, path) in SPECIFIC_RULES:
        return SPECIFIC_RULES[(method, path)]
    if path.startswith(f"{API}/finances/"):
        return {ADMIN, FIN}
    if path.startswith(f"{API}/scientific/"):
        return {ADMIN, SCI}
    if path.startswith(f"{API}/sante/"):
        return {ADMIN, SANTE}  # décision Partie 6 : accès complet sante + admin
    return {ADMIN}


def all_routes():
    """Routes déclarées, lues dans le schéma OpenAPI : API publique de FastAPI, stable entre versions
    (la structure interne de app.routes a changé en FastAPI 0.16x)."""
    for path, operations in sorted(app.openapi()["paths"].items()):
        for method, operation in sorted(operations.items()):
            query = {p["name"] for p in operation.get("parameters", []) if p.get("in") == "query"}
            yield method.upper(), SimpleNamespace(path=path, query_params=query)


ROUTES = list(all_routes())
# Nombre de routes de l'API au moment de la Partie 2 (à ajuster quand une route est ajoutée/retirée)
MIN_EXPECTED_ROUTES = 87  # 75 + 8 Santé (Partie 6) + 4 paiement/export (Partie 8)
PROTECTED = [(m, r) for m, r in ROUTES if expected_access(m, r.path) is not None]
ROUTE_IDS = [f"{m} {r.path}" for m, r in ROUTES]
PROTECTED_IDS = [f"{m} {r.path}" for m, r in PROTECTED]


def hs384_token_with_app_key():
    """Token signé avec la bonne clé mais un autre algorithme (doit être refusé).
    La clé de test est courte pour HS384 : l'avertissement de PyJWT est attendu ici seulement."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", jwt.warnings.InsecureKeyLengthWarning)
        return jwt.encode({"sub": IDS[ADMIN]}, SECRET_KEY, algorithm="HS384")


def concrete_path(path):
    return re.sub(r"\{[^}]+\}", "x", path)


# ---------------------------------------------------------------------------
# Faux Prisma (aucune base réelle)
# ---------------------------------------------------------------------------

# Hash calculé une seule fois (Argon2 est volontairement lent)
PASSWORD_HASH = get_password_hash("motdepasse-fictif")


def make_user(user_id, role, is_active=True):
    now = datetime.now(timezone.utc)
    return User(
        id=user_id, email=f"{user_id[-4:]}@exemple.test", username=f"user{user_id[-4:]}",
        password=PASSWORD_HASH, nom="Fictif", prenom="Test", role=role,
        is_active=is_active, last_login=None, created_at=now, updated_at=now,
    )


IDS = {
    ADMIN: "a" * 24, SCI: "b" * 24, FIN: "c" * 24, SANTE: "d" * 24,
    "inactive": "e" * 24, "legacy": "f" * 24, "other_admin": "1" * 24,
}


class FakeUserActions:
    def __init__(self):
        self.users = {
            IDS[ADMIN]: make_user(IDS[ADMIN], ADMIN),
            IDS[SCI]: make_user(IDS[SCI], SCI),
            IDS[FIN]: make_user(IDS[FIN], FIN),
            IDS[SANTE]: make_user(IDS[SANTE], SANTE),
            IDS["inactive"]: make_user(IDS["inactive"], ADMIN, is_active=False),
            IDS["legacy"]: make_user(IDS["legacy"], "finance"),
            IDS["other_admin"]: make_user(IDS["other_admin"], ADMIN),
        }
        self.updates = []

    async def find_unique(self, where):
        return self.users.get(where.get("id"))

    async def find_first(self, where):
        wanted = {c.get("email") or c.get("username") for c in where["OR"]}
        return next((u for u in self.users.values() if u.email in wanted or u.username in wanted), None)

    async def update(self, where, data):
        self.updates.append((where["id"], data))
        user = self.users[where["id"]].model_copy(update=data)
        self.users[where["id"]] = user
        return user


@pytest.fixture
def fake_users(monkeypatch):
    fake = FakeUserActions()
    monkeypatch.setattr(prisma, "user", fake)
    return fake


@pytest.fixture
def client(fake_users):
    # Sans "with" : le lifespan (connexion MongoDB) n'est pas exécuté
    return TestClient(app, raise_server_exceptions=False)


def auth(role_key):
    return {"Authorization": f"Bearer {create_access_token({'sub': IDS[role_key]})}"}


def call(client, method, route, headers=None):
    return client.request(method, concrete_path(route.path), headers=headers or {})


# ---------------------------------------------------------------------------
# Matrice des droits
# ---------------------------------------------------------------------------

def test_route_discovery_is_complete():
    # Garde-fou : si la découverte des routes casse (changement interne de FastAPI), les tests
    # paramétrés disparaîtraient silencieusement au lieu d'échouer
    assert len(ROUTES) >= MIN_EXPECTED_ROUTES


@pytest.mark.parametrize("method,route", ROUTES, ids=ROUTE_IDS)
def test_route_access_matches_matrix(client, method, route):
    """Vérification comportementale : un anonyme est refusé (401) partout sauf sur les routes publiques."""
    status = call(client, method, route).status_code
    if expected_access(method, route.path) is None:
        assert status not in (401, 403)
    else:
        assert status == 401


def test_every_public_route_exists():
    existing = {(m, r.path) for m, r in ROUTES}
    assert PUBLIC_ROUTES <= existing


def test_no_route_takes_author_from_client():
    forbidden = {"created_by", "deleted_by", "generated_by", "modified_by", "restored_by"}
    for method, route in ROUTES:
        names = route.query_params
        assert not names & forbidden, f"{method} {route.path}"


# ---------------------------------------------------------------------------
# Comportement des gardes sur chaque route protégée
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("method,route", PROTECTED, ids=PROTECTED_IDS)
def test_protected_route_rejects_anonymous(client, method, route):
    response = call(client, method, route)
    assert response.status_code == 401
    assert response.headers.get("www-authenticate") == "Bearer"


@pytest.mark.parametrize("method,route", PROTECTED, ids=PROTECTED_IDS)
def test_protected_route_rejects_bad_tokens(client, method, route):
    now = datetime.now(timezone.utc)
    bad_tokens = [
        "pas-un-jwt",
        jwt.encode({"sub": IDS[ADMIN], "exp": now + timedelta(hours=1)}, "autre-cle-de-signature-quelconque-123", algorithm=ALGORITHM),
        jwt.encode({"sub": IDS[ADMIN], "exp": now - timedelta(minutes=1)}, SECRET_KEY, algorithm=ALGORITHM),
        hs384_token_with_app_key(),
        create_access_token({"sub": "pas-un-objectid"}),
        create_access_token({"sub": "9" * 24}),  # utilisateur inexistant
        create_access_token({"email": "sans-sub@exemple.test"}),
    ]
    for token in bad_tokens:
        response = call(client, method, route, {"Authorization": f"Bearer {token}"})
        assert response.status_code == 401, token[:20]
    assert call(client, method, route, {"Authorization": "Basic abc"}).status_code == 401


@pytest.mark.parametrize("method,route", PROTECTED, ids=PROTECTED_IDS)
def test_protected_route_rejects_inactive_account(client, method, route):
    assert call(client, method, route, auth("inactive")).status_code == 403


@pytest.mark.parametrize("method,route", PROTECTED, ids=PROTECTED_IDS)
def test_protected_route_enforces_roles(client, method, route):
    allowed = expected_access(method, route.path)
    allowed = set(ROLES) if allowed == ANY else allowed
    for role in ROLES:
        status = call(client, method, route, auth(role)).status_code
        if role in allowed:
            # La garde laisse passer (le handler échoue ensuite faute de base : 4xx/5xx autres que 401/403)
            assert status not in (401, 403), role
        else:
            assert status == 403, role


# ---------------------------------------------------------------------------
# Rôles, comptes et JWT
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("admin", ADMIN), ("ADMINISTRATION", ADMIN), ("finance", FIN), ("Finances", FIN),
    ("scientific", SCI), ("SCIENTIFIQUE", SCI), ("SANTE", SANTE), ("santé", SANTE),
    ("consultation", "consultation"),
])
def test_normalize_role(raw, expected):
    assert normalize_role(raw) == expected


def test_legacy_role_is_normalized_and_authorized(client):
    response = client.get(f"{API}/admin/auth/me", headers=auth("legacy"))
    assert response.status_code == 200
    assert response.json()["role"] == FIN
    assert "password" not in response.json()
    assert client.get(f"{API}/finances/sync/status", headers=auth("legacy")).status_code not in (401, 403)


def test_unknown_role_has_no_access(client, fake_users):
    fake_users.users["2" * 24] = make_user("2" * 24, "consultation")
    headers = {"Authorization": f"Bearer {create_access_token({'sub': '2' * 24})}"}
    assert client.get(f"{API}/admin/auth/me", headers=headers).status_code == 200
    assert client.get(f"{API}/finances/dashboard", headers=headers).status_code == 403
    assert client.get(f"{API}/registrations", headers=headers).status_code == 403


@pytest.mark.parametrize("payload", [
    {"role": "superadmin"},
    {"role": "consultation"},
    {"password": "court"},
])
def test_update_user_rejects_invalid_values(client, payload):
    response = client.put(f"{API}/admin/users/{IDS[SCI]}", json=payload, headers=auth(ADMIN))
    assert response.status_code == 422


def test_create_user_rejects_unknown_role(client):
    body = {"email": "n@exemple.test", "username": "nouveau", "password": "motdepasse-fictif",
            "nom": "N", "prenom": "P", "role": "superadmin"}
    assert client.post(f"{API}/admin/auth/register", json=body, headers=auth(ADMIN)).status_code == 422


def test_non_admin_cannot_change_another_account(client, fake_users):
    for role in (SCI, FIN, SANTE):
        response = client.put(f"{API}/admin/users/{IDS[ADMIN]}", json={"password": "nouveau-mdp-fictif"}, headers=auth(role))
        assert response.status_code == 403
    assert fake_users.updates == []


@pytest.mark.parametrize("payload", [{"is_active": False}, {"role": "finances"}])
def test_admin_cannot_disable_or_demote_self(client, fake_users, payload):
    response = client.put(f"{API}/admin/users/{IDS[ADMIN]}", json=payload, headers=auth(ADMIN))
    assert response.status_code == 400
    assert fake_users.updates == []


def test_admin_cannot_toggle_self(client, fake_users):
    assert client.patch(f"{API}/admin/users/{IDS[ADMIN]}", headers=auth(ADMIN)).status_code == 400
    assert fake_users.updates == []


def test_admin_can_toggle_other_account(client, fake_users):
    response = client.patch(f"{API}/admin/users/{IDS['other_admin']}", headers=auth(ADMIN))
    assert response.status_code == 200
    assert response.json()["is_active"] is False
    assert fake_users.updates == [(IDS["other_admin"], {"is_active": False})]

    response = client.patch(f"{API}/admin/users/{IDS['other_admin']}", headers=auth(ADMIN))
    assert response.json()["is_active"] is True


def test_deactivated_user_loses_access_immediately(client, fake_users):
    headers = auth(SCI)
    assert client.get(f"{API}/admin/auth/me", headers=headers).status_code == 200
    fake_users.users[IDS[SCI]] = fake_users.users[IDS[SCI]].model_copy(update={"is_active": False})
    assert client.get(f"{API}/admin/auth/me", headers=headers).status_code == 403


def test_login_returns_token_with_configured_lifetime(client, fake_users):
    user = fake_users.users[IDS["legacy"]]
    response = client.post(f"{API}/admin/auth/login", json={"identifier": user.email, "password": "motdepasse-fictif"})
    assert response.status_code == 200
    body = response.json()
    assert body["user"]["role"] == FIN
    assert "password" not in body["user"]
    claims = jwt.decode(body["access_token"], SECRET_KEY, algorithms=[ALGORITHM])
    assert claims["sub"] == IDS["legacy"]
    assert claims["exp"] - claims["iat"] == 480 * 60


def test_login_failures_are_indistinguishable(client, fake_users):
    user = fake_users.users[IDS[ADMIN]]
    wrong_password = client.post(f"{API}/admin/auth/login", json={"identifier": user.email, "password": "mauvais-mdp"})
    unknown_user = client.post(f"{API}/admin/auth/login", json={"identifier": "inconnu@exemple.test", "password": "mauvais-mdp"})
    assert wrong_password.status_code == unknown_user.status_code == 401
    assert wrong_password.json() == unknown_user.json()


def test_role_from_database_not_from_token(client):
    # Un token qui prétend "admin" pour un compte "sante" n'ouvre pas les routes admin
    token = create_access_token({"sub": IDS[SANTE], "role": ADMIN})
    response = client.get(f"{API}/admin/users", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 403
