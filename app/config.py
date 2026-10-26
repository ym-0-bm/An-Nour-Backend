from typing import Annotated, List, Literal, Optional
import hashlib
import json
from pathlib import Path

from pydantic import Field, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsError

# Empreinte SHA-256 de l'ancienne clé JWT par défaut (publique dans l'historique Git) : toujours refusée
LEGACY_SECRET_KEY_SHA256 = "869885f9f45a0e201f35b957e1e28c2cfe013b4d185ef4301ebfaf2cdef5c424"
SECRET_KEY_MIN_LENGTH = 32

# Origines ajoutées hors production pour les tests en local
LOCAL_CORS_ORIGINS = ["http://localhost:3000"]


class Settings(BaseSettings):
    # Par défaut "production" : le mode le plus strict s'applique si la variable est oubliée
    ENVIRONMENT: Literal["development", "test", "production"] = "production"

    DATABASE_URL: str
    API_V1_STR: str = "/api/v1"
    PROJECT_NAME: str = "Inscription System"
    MEDIA_DIR: str = "media"

    # JWT
    SECRET_KEY: str
    # 8 h : une journée de travail, sous le plafond de 12 h recommandé (NIST SP 800-63B, AAL2)
    # tant qu'aucun refresh token n'existe
    ACCESS_TOKEN_EXPIRE_MINUTES: int = Field(default=480, ge=5, le=720)

    # Tarif unique d'inscription (FCFA) : seule référence pour la vérification des reçus et les finances
    REGISTRATION_FEE: int = Field(default=6000, gt=0)

    # Matricules (B8) : ANNOUR + 2 chiffres de l'année ; MATRICULE_YEAR force l'année, sinon année courante
    MATRICULE_PREFIX: str = Field(default="ANNOUR", pattern=r"^[A-Z]{2,12}$")
    MATRICULE_YEAR: Optional[int] = Field(default=None, ge=2000, le=2099)

    # Téléversements d'images (reçus, photos)
    MAX_UPLOAD_BYTES: int = Field(default=5 * 1024 * 1024, gt=0)

    # CORS : liste séparée par des virgules ou JSON, ex. CORS_ORIGINS=https://a.app,https://b.app
    CORS_ORIGINS: Annotated[List[str], NoDecode] = [
        "https://an-nour25.vercel.app",
        "https://annourpwa.netlify.app",
    ]

    # Cloudinary
    CLOUDINARY_CLOUD_NAME: str
    CLOUDINARY_API_KEY: str
    CLOUDINARY_API_SECRET: str

    class Config:
        # Check for Render's secret file location first, then local .env
        env_file = (
            "/etc/secrets/.env" if Path("/etc/secrets/.env").exists()
            else ".env"
        )

    @field_validator("SECRET_KEY")
    @classmethod
    def check_secret_key(cls, value: str) -> str:
        if len(value) < SECRET_KEY_MIN_LENGTH:
            raise ValueError(f"doit contenir au moins {SECRET_KEY_MIN_LENGTH} caractères")
        if hashlib.sha256(value.encode("utf-8")).hexdigest() == LEGACY_SECRET_KEY_SHA256:
            raise ValueError("l'ancienne clé par défaut est compromise, générer une nouvelle clé")
        return value

    @field_validator("CORS_ORIGINS", mode="before")
    @classmethod
    def parse_cors_origins(cls, value):
        if isinstance(value, str):
            value = value.strip()
            if value.startswith("["):
                value = json.loads(value)
            else:
                value = value.split(",")
        return [origin.strip().rstrip("/") for origin in value if origin and origin.strip()]

    @model_validator(mode="after")
    def check_cors_origins(self):
        if self.ENVIRONMENT == "production" and "*" in self.CORS_ORIGINS:
            raise ValueError("CORS_ORIGINS ne doit pas contenir '*' en production")
        return self

    @property
    def cors_origins(self) -> List[str]:
        if self.ENVIRONMENT == "production":
            return list(self.CORS_ORIGINS)
        return list(self.CORS_ORIGINS) + [o for o in LOCAL_CORS_ORIGINS if o not in self.CORS_ORIGINS]


def load_settings(**overrides) -> Settings:
    """Charger la configuration ; en cas d'erreur, ne révéler que les noms de variables, jamais leurs valeurs."""
    try:
        return Settings(**overrides)
    except ValidationError as exc:
        details = "; ".join(
            f"{'.'.join(str(part) for part in err['loc']) or 'configuration'}: {err['msg']}"
            for err in exc.errors()
        )
        raise RuntimeError(f"Configuration invalide — {details}") from None
    except SettingsError as exc:
        # Le message de SettingsError ne contient que le nom du champ et la source
        raise RuntimeError(f"Configuration invalide — {exc}") from None


settings = load_settings()
