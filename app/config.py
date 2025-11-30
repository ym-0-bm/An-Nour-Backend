from pydantic_settings import BaseSettings
import os
from dotenv import load_dotenv

class Settings(BaseSettings):
    DATABASE_URL: str
    API_V1_STR: str = "/api/v1"
    PROJECT_NAME: str = "Inscription System"
    MEDIA_DIR: str = "media"

    # Cloudinary
    CLOUDINARY_CLOUD_NAME: str = "dhyzc2lme"
    CLOUDINARY_API_KEY: str = "158713382696999"
    CLOUDINARY_API_SECRET: str = "sP1F8_lIFwnEAD14udb-7Yxr5EU"

    class Config:
        env_file = ".env"


settings = Settings()