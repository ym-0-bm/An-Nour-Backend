from pydantic_settings import BaseSettings
import os
from pathlib import Path

class Settings(BaseSettings):
    DATABASE_URL: str
    API_V1_STR: str = "/api/v1"
    PROJECT_NAME: str = "Inscription System"
    MEDIA_DIR: str = "media"

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


settings = Settings()