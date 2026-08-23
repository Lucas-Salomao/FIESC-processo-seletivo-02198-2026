"""Dependências injetáveis da API (substituíveis nos testes)."""

from collections.abc import Generator

from sqlalchemy.orm import Session

from src.core.db import get_session_factory
from src.llm.client import LLMClient, get_llm_client


def get_db() -> Generator[Session, None, None]:
    """Abre uma sessão do banco para a duração de uma requisição e garante
    que ela seja fechada ao final, mesmo se a rota lançar uma exceção."""
    factory = get_session_factory()
    session = factory()
    try:
        yield session
    finally:
        session.close()


def get_llm() -> LLMClient:
    """Cliente do LLM (Gemini) usado pelos endpoints — nos testes é substituído
    por um FakeLLMClient via `app.dependency_overrides`."""
    return get_llm_client()


def get_chat_agent():
    """Agente ADK do chat, ou None para acionar o fallback RAG (overridável em testes)."""
    from src.llm.agent import get_agent

    return get_agent()
