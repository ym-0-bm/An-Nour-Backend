import os
from urllib.parse import urlparse

import pytest

# Base injoignable (port 1) : un test unitaire ne peut jamais atteindre une vraie base,
# même si un MongoDB tourne localement sur le port par défaut
UNREACHABLE_DATABASE_URL = "mongodb://127.0.0.1:1/annour_unit_test_unreachable"

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
INTEGRATION_URL = os.environ.get("INTEGRATION_DATABASE_URL")


def check_safe_integration_target(url: str) -> str:
    """Refuser toute base qui n'est pas locale et dont le nom ne contient pas 'test'."""
    parsed = urlparse(url)
    db_name = parsed.path.lstrip("/")
    if parsed.hostname not in LOCAL_HOSTS or "test" not in db_name.lower():
        pytest.exit(
            "INTEGRATION_DATABASE_URL doit désigner une base locale dont le nom contient 'test' "
            "(refus de s'exécuter sur une autre base).",
            returncode=2,
        )
    return db_name


if INTEGRATION_URL:
    check_safe_integration_target(INTEGRATION_URL)

# Configuration fictive pour les tests : définie avant tout import de l'application
# (les variables d'environnement priment sur le fichier .env local)
TEST_ENV = {
    "ENVIRONMENT": "test",
    "DATABASE_URL": INTEGRATION_URL or UNREACHABLE_DATABASE_URL,
    "SECRET_KEY": "test-only-secret-key-0123456789abcdef",
    "CLOUDINARY_CLOUD_NAME": "test_cloud",
    "CLOUDINARY_API_KEY": "test_api_key",
    "CLOUDINARY_API_SECRET": "test_api_secret",
}

for key, value in TEST_ENV.items():
    os.environ[key] = value


@pytest.fixture(autouse=True)
def reset_rate_limiter():
    # Chaque test démarre avec des compteurs de débit vierges
    from app.utils.rate_limit import limiter
    limiter.clear()
    yield
    limiter.clear()
