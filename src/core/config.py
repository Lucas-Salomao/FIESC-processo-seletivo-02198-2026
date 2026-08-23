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
    """Todas as configurações da aplicação, lidas do arquivo .env (ou de variáveis
    de ambiente reais, como em produção). Cada campo abaixo já tem um valor padrão
    pensado para rodar localmente sem precisar configurar nada."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # String de conexão com o banco de dados corporativo (eventos, diagnósticos, documentos...).
    database_url: str = "postgresql+psycopg2://fiesc:fiesc@localhost:5432/fiesc"

    # --- Vertex AI / Gemini: usados pelo chat, pelas prescrições (RAG) e pelo OCR de PDFs ---
    google_cloud_project: str = ""
    google_cloud_location: str = "global"  # gemini-3.6-flash/embedding-2 vivem no endpoint global
    google_application_credentials: str = ""  # caminho do JSON da service account (dev local)
    google_credentials_b64: str = ""  # mesmo JSON em base64 (deploy — evita arquivo no repo)
    gemini_chat_model: str = "gemini-3.6-flash"
    gemini_embedding_model: str = "gemini-embedding-2"
    embedding_dim: int = 768  # dimensão do vetor de embedding gerado pelo Gemini

    # --- Base documental (RAG), indexada no ChromaDB ---
    chroma_dir: Path = Path("./data/chroma")  # pasta onde o ChromaDB persiste os embeddings
    rag_top_k: int = 6  # quantos trechos de documento buscar por consulta ao RAG

    # --- Broker MQTT usado pelos sensores e pelo worker de ingestão ---
    mqtt_host: str = "localhost"
    mqtt_port: int = 1883
    mqtt_telemetry_topic: str = "sensors/+/telemetry"  # "+" = curinga: qualquer máquina
    mqtt_username: str = ""
    mqtt_password: str = ""

    # --- Diversos ---
    api_url: str = "http://localhost:8000"  # endereço da API — usado pela UI e pelo worker MQTT
    artifacts_dir: Path = Path("./artifacts")  # pasta com o modelo treinado (scaler, lgbm, knn)

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
        # Já está no formato certo (ou é sqlite, usado só nos testes)? Não faz nada.
        for prefix in ("postgresql+", "sqlite"):
            if value.startswith(prefix):
                return value
        # Caso contrário, insere o driver "+psycopg2" no prefixo da URL.
        if value.startswith("postgresql://"):
            return value.replace("postgresql://", "postgresql+psycopg2://", 1)
        if value.startswith("postgres://"):
            return value.replace("postgres://", "postgresql+psycopg2://", 1)
        return value


@lru_cache
def get_settings() -> Settings:
    """Devolve a configuração da aplicação. `lru_cache` garante que o .env só é
    lido uma vez por processo e a mesma instância é reaproveitada depois."""
    return Settings()


def ensure_google_env() -> None:
    """Exporta p/ os.environ o que as libs Google leem diretamente do ambiente.

    pydantic-settings carrega o .env apenas para o objeto Settings; google-auth
    e o ADK leem os.environ — esta função faz a ponte (idempotente).
    """
    s = get_settings()

    # Garante que o caminho do arquivo de credenciais (se existir) esteja
    # disponível na variável de ambiente que as bibliotecas do Google leem.
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
    # Caso 1: já temos o caminho de um arquivo local (desenvolvimento).
    if s.google_application_credentials:
        return str(Path(s.google_application_credentials).resolve())
    # Caso 2: sem arquivo local e sem credencial em base64 — nada a fazer.
    if not s.google_credentials_b64:
        return None

    # Caso 3: credencial em base64 (produção) — decodifica e valida o JSON.
    try:
        raw = base64.b64decode(s.google_credentials_b64, validate=True)
        json.loads(raw)  # falha cedo se o conteúdo não for um JSON válido
    except (binascii.Error, ValueError, UnicodeDecodeError) as exc:
        log.error("GOOGLE_CREDENTIALS_B64 inválido (%s) — Vertex AI ficará indisponível.", exc)
        return None

    # Grava o JSON decodificado em um arquivo temporário, só se ainda não
    # existir (ou se o conteúdo mudou) — evita reescrever em disco sem necessidade.
    target = Path(tempfile.gettempdir()) / "gcp-service-account.json"
    if not target.exists() or target.read_bytes() != raw:
        target.write_bytes(raw)
        target.chmod(0o600)  # restringe a leitura ao dono do processo
    return str(target)
