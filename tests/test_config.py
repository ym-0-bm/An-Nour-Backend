import hashlib

import pytest
from fastapi import HTTPException
import jwt

from app.config import LEGACY_SECRET_KEY_SHA256, Settings, load_settings
from tests.conftest import TEST_ENV

VALID = {k: v for k, v in TEST_ENV.items()}


def make(**overrides):
    values = {**VALID, **overrides}
    return load_settings(_env_file=None, **values)


def test_valid_configuration_loads():
    settings = make()
    assert settings.ENVIRONMENT == "test"
    assert settings.SECRET_KEY == VALID["SECRET_KEY"]


@pytest.mark.parametrize("missing", ["SECRET_KEY", "DATABASE_URL", "CLOUDINARY_API_SECRET"])
def test_missing_required_variable_fails(monkeypatch, missing):
    monkeypatch.delenv(missing, raising=False)
    values = {k: v for k, v in VALID.items() if k != missing}
    with pytest.raises(RuntimeError, match=missing):
        load_settings(_env_file=None, **values)


def test_short_secret_key_rejected():
    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        make(SECRET_KEY="trop-court")


def test_legacy_secret_key_rejected(monkeypatch):
    # Simule une clé dont l'empreinte correspond à l'ancienne valeur par défaut
    candidate = "x" * 40
    monkeypatch.setattr(
        "app.config.LEGACY_SECRET_KEY_SHA256", hashlib.sha256(candidate.encode()).hexdigest()
    )
    with pytest.raises(RuntimeError, match="compromise"):
        make(SECRET_KEY=candidate)
    assert LEGACY_SECRET_KEY_SHA256 != hashlib.sha256(VALID["SECRET_KEY"].encode()).hexdigest()


def test_error_message_does_not_leak_values():
    secret = "valeur-secrete-courte"
    with pytest.raises(RuntimeError) as exc:
        make(SECRET_KEY=secret)
    assert secret not in str(exc.value)
    assert exc.value.__cause__ is None
    assert exc.value.__suppress_context__


def test_default_environment_is_production(monkeypatch):
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    values = {k: v for k, v in VALID.items() if k != "ENVIRONMENT"}
    assert load_settings(_env_file=None, **values).ENVIRONMENT == "production"


def test_wildcard_cors_rejected_in_production():
    with pytest.raises(RuntimeError, match="CORS_ORIGINS"):
        make(ENVIRONMENT="production", CORS_ORIGINS=["*"])


def test_production_cors_origins_exclude_localhost():
    origins = make(ENVIRONMENT="production").cors_origins
    assert origins == ["https://an-nour25.vercel.app", "https://annourpwa.netlify.app"]
    assert "*" not in origins


def test_development_cors_origins_include_localhost():
    origins = make(ENVIRONMENT="development").cors_origins
    assert "http://localhost:3000" in origins
    assert "https://an-nour25.vercel.app" in origins


def test_cors_origins_from_environment_json(monkeypatch):
    monkeypatch.setenv("CORS_ORIGINS", '["https://exemple.test"]')
    assert Settings(_env_file=None).CORS_ORIGINS == ["https://exemple.test"]


def test_cors_origins_from_environment_comma_separated(monkeypatch):
    monkeypatch.setenv("CORS_ORIGINS", " https://a.test/ , https://b.test,")
    assert Settings(_env_file=None).CORS_ORIGINS == ["https://a.test", "https://b.test"]


def test_cors_origins_invalid_json_fails_cleanly(monkeypatch):
    monkeypatch.setenv("CORS_ORIGINS", '["https://a.test"')
    with pytest.raises(RuntimeError, match="CORS_ORIGINS"):
        load_settings(_env_file=None)


def test_jwt_uses_configured_key_and_rejects_foreign_key():
    from app.utils.auth import SECRET_KEY, create_access_token, decode_token

    assert SECRET_KEY == VALID["SECRET_KEY"]
    token = create_access_token({"sub": "utilisateur-fictif"})
    assert decode_token(token)["sub"] == "utilisateur-fictif"

    forged = jwt.encode({"sub": "admin-fictif"}, "une-autre-cle-de-signature-quelconque", algorithm="HS256")
    with pytest.raises(HTTPException) as exc:
        decode_token(forged)
    assert exc.value.status_code == 401


def test_app_cors_middleware_uses_settings():
    from app.main import app
    from starlette.middleware.cors import CORSMiddleware

    cors = [m for m in app.user_middleware if m.cls is CORSMiddleware]
    assert len(cors) == 1
    assert "*" not in cors[0].kwargs["allow_origins"]
