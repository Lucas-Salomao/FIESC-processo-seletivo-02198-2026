"""Configuração central — tudo vem de variáveis de ambiente (.env)."""

import base64
import binascii
import json
import logging
import os
import tempfile
from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

log = logging.getLogger("config")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "postgresql+psycopg2://fiesc:fiesc@localhost:5432/fiesc"

    google_cloud_project: str = ""
    google_cloud_location: str = "global"  # gemini-3.6-flash/embedding-2 vivem no endpoint global
    google_application_credentials: str = ""  # caminho do JSON da service account (dev local)
    google_credentials_b64: str = ""  # mesmo JSON em base64 (deploy — evita arquivo no repo)
    gemini_chat_model: str = "gemini-3.6-flash"
    gemini_embedding_model: str = "gemini-embedding-2"
    embedding_dim: int = 768

    chroma_dir: Path = Path("./data/chroma")
    rag_top_k: int = 6

    mqtt_host: str = "localhost"
    mqtt_port: int = 1883
    mqtt_telemetry_topic: str = "sensors/+/telemetry"
    mqtt_username: str = ""
    mqtt_password: str = ""

    api_url: str = "http://localhost:8000"
    artifacts_dir: Path = Path("./artifacts")

    # Ingestão automática dos PDFs no primeiro boot (volume vazio).
    rag_bootstrap: bool = False
    docs_dir: Path = Path("./documentos")

    @field_validator("database_url")
    @classmethod
    def _normalize_database_url(cls, value: str) -> str:
        """Aceita a URL no formato que provedores gerenciados entregam.

        Railway, Heroku e afins expõem `postgresql://` (ou o legado
        `postgres://`); o projeto usa psycopg2 explicitamente. Normalizar aqui
        evita ter que reescrever a variável no painel de cada ambiente.
        """
        for prefix in ("postgresql+", "sqlite"):
            if value.startswith(prefix):
                return value
        if value.startswith("postgresql://"):
            return value.replace("postgresql://", "postgresql+psycopg2://", 1)
        if value.startswith("postgres://"):
            return value.replace("postgres://", "postgresql+psycopg2://", 1)
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()


def ensure_google_env() -> None:
    """Exporta p/ os.environ o que as libs Google leem diretamente do ambiente.

    pydantic-settings carrega o .env apenas para o objeto Settings; google-auth
    e o ADK leem os.environ — esta função faz a ponte (idempotente).
    """
    s = get_settings()

    credentials_path = _materialize_credentials(s)
    if credentials_path:
        os.environ.setdefault("GOOGLE_APPLICATION_CREDENTIALS", credentials_path)
    if s.google_cloud_project:
        os.environ.setdefault("GOOGLE_CLOUD_PROJECT", s.google_cloud_project)
    os.environ.setdefault("GOOGLE_CLOUD_LOCATION", s.google_cloud_location)
    os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "TRUE")


def _materialize_credentials(s: Settings) -> str | None:
    """Devolve o caminho do JSON da service account.

    Em desenvolvimento o arquivo já existe no disco. Em deploy ele chega como
    base64 numa variável de ambiente (o JSON nunca entra no repositório nem na
    imagem) e é escrito em um arquivo temporário com permissão restrita.
    """
    if s.google_application_credentials:
        return str(Path(s.google_application_credentials).resolve())
    if not s.google_credentials_b64:
        return None

    try:
        raw = base64.b64decode(s.google_credentials_b64, validate=True)
        json.loads(raw)  # falha cedo se o conteúdo não for um JSON válido
    except (binascii.Error, ValueError, UnicodeDecodeError) as exc:
        log.error("GOOGLE_CREDENTIALS_B64 inválido (%s) — Vertex AI ficará indisponível.", exc)
        return None

    target = Path(tempfile.gettempdir()) / "gcp-service-account.json"
    if not target.exists() or target.read_bytes() != raw:
        target.write_bytes(raw)
        target.chmod(0o600)
    return str(target)
