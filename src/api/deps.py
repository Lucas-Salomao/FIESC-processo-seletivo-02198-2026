"""Dependências injetáveis da API (substituíveis nos testes)."""

from collections.abc import Generator

from sqlalchemy.orm import Session

from src.core.db import get_session_factory
from src.llm.client import LLMClient, get_llm_client


def get_db() -> Generator[Session, None, None]:
    factory = get_session_factory()
    session = factory()
    try:
        yield session
    finally:
        session.close()


def get_llm() -> LLMClient:
    return get_llm_client()


def get_chat_agent():
    """Agente ADK do chat, ou None para acionar o fallback RAG (overridável em testes)."""
    from src.llm.agent import get_agent

    return get_agent()
