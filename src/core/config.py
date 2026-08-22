"""Configuração central — tudo vem de variáveis de ambiente (.env)."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "postgresql+psycopg2://fiesc:fiesc@localhost:5432/fiesc"

    google_cloud_project: str = ""
    google_cloud_location: str = "global"  # gemini-3.6-flash/embedding-2 vivem no endpoint global
    google_application_credentials: str = ""  # caminho do JSON da service account (opcional)
    gemini_chat_model: str = "gemini-3.6-flash"
    gemini_embedding_model: str = "gemini-embedding-2"
    embedding_dim: int = 768

    chroma_dir: Path = Path("./data/chroma")
    rag_top_k: int = 6

    mqtt_host: str = "localhost"
    mqtt_port: int = 1883
    mqtt_telemetry_topic: str = "sensors/+/telemetry"

    api_url: str = "http://localhost:8000"
    artifacts_dir: Path = Path("./artifacts")


@lru_cache
def get_settings() -> Settings:
    return Settings()


def ensure_google_env() -> None:
    """Exporta p/ os.environ o que as libs Google leem diretamente do ambiente.

    pydantic-settings carrega o .env apenas para o objeto Settings; google-auth
    e o ADK leem os.environ — esta função faz a ponte (idempotente).
    """
    import os

    s = get_settings()
    if s.google_application_credentials:
        os.environ.setdefault(
            "GOOGLE_APPLICATION_CREDENTIALS",
            str(Path(s.google_application_credentials).resolve()),
        )
    if s.google_cloud_project:
        os.environ.setdefault("GOOGLE_CLOUD_PROJECT", s.google_cloud_project)
    os.environ.setdefault("GOOGLE_CLOUD_LOCATION", s.google_cloud_location)
    os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "TRUE")
