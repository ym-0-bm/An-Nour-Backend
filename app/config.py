from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    DATABASE_URL: str
    API_V1_STR: str = "/api/v1"
    PROJECT_NAME: str = "Inscription System"
    MEDIA_DIR: str = "media"

    class Config:
        env_file = ".env"


settings = Settings()