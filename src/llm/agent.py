"""Agente de manutenção prescritiva — Google ADK.

Cérebro do /chat: LlmAgent (gemini-3.6-flash via Vertex AI) com 4 ferramentas
que reutilizam o código existente (Postgres, ChromaDB, DiagnosisEngine).
Sessões de conversa persistidas no PostgreSQL (DatabaseSessionService).

O caminho crítico POST /diagnose NÃO passa por aqui — permanece determinístico.
Se a inicialização falhar (sem credenciais GCP, sem dependência), get_agent()
retorna None e o /chat cai no fallback RAG de um passo.
"""

import logging
import uuid

from src.core.config import get_settings
from src.core.schemas import ChatResponse, Citation
from src.llm.agent_tools import AGENT_TOOLS, get_citations, reset_citations
from src.llm.prompts import SYSTEM_AGENT

log = logging.getLogger("agent")

APP_NAME = "fiesc-pmx"
_agent_singleton: "MaintenanceAgent | None | bool" = False  # False = ainda não tentado


def _async_db_url(url: str) -> str:
    """DatabaseSessionService (ADK 2.x) exige driver assíncrono."""
    return url.replace("+psycopg2", "+asyncpg").replace("sqlite://", "sqlite+aiosqlite://")


class MaintenanceAgent:
    """Encapsula o LlmAgent do Google ADK: monta o agente com suas 4
    ferramentas e o Runner responsável por executar cada mensagem do chat
    mantendo a memória de conversa entre chamadas (por `session_id`)."""

    def __init__(self) -> None:
        from google.adk.agents import LlmAgent
        from google.adk.runners import Runner
        from google.adk.sessions import DatabaseSessionService

        from src.core.config import ensure_google_env

        settings = get_settings()
        if not settings.google_cloud_project:
            raise RuntimeError("GOOGLE_CLOUD_PROJECT não configurado.")
        # O ADK lê as credenciais/projeto do os.environ (mesmas vars do google-genai)
        ensure_google_env()

        self._agent = LlmAgent(
            name="assistente_manutencao",
            model=settings.gemini_chat_model,
            instruction=SYSTEM_AGENT,
            tools=list(AGENT_TOOLS),
        )
        self._runner = Runner(
            app_name=APP_NAME,
            agent=self._agent,
            session_service=DatabaseSessionService(db_engine=self._sessions_engine(settings)),
            auto_create_session=True,
        )

    @staticmethod
    def _sessions_engine(settings):
        """Engine assíncrona p/ as sessões do ADK em schema dedicado 'adk'.

        O ADK cria tabelas próprias ('sessions', 'events', ...) — a tabela
        'events' colide com a do nosso domínio, então isolamos via search_path.
        """
        from sqlalchemy import text
        from sqlalchemy.ext.asyncio import create_async_engine

        from src.core.db import get_engine

        async_url = _async_db_url(settings.database_url)
        if async_url.startswith("postgresql"):
            with get_engine().begin() as conn:
                conn.execute(text("CREATE SCHEMA IF NOT EXISTS adk"))
            return create_async_engine(
                async_url, connect_args={"server_settings": {"search_path": "adk"}}
            )
        return create_async_engine(async_url)

    async def ask(self, message: str, session_id: str | None, user_id: str = "ui") -> ChatResponse:
        """Envia a mensagem do usuário ao agente e devolve a resposta final,
        já com as citações coletadas durante o uso das ferramentas (RAG)."""
        from google.genai import types

        session_id = session_id or str(uuid.uuid4())
        reset_citations()  # zera o coletor de citações desta pergunta

        # O ADK emite vários eventos intermediários (chamadas de ferramenta,
        # passos de raciocínio); só nos interessa o texto da resposta final.
        answer = ""
        async for event in self._runner.run_async(
            user_id=user_id,
            session_id=session_id,
            new_message=types.Content(role="user", parts=[types.Part(text=message)]),
        ):
            if event.is_final_response() and event.content and event.content.parts:
                answer = "".join(p.text or "" for p in event.content.parts)

        return ChatResponse(
            answer_md=answer or "Não consegui gerar uma resposta — tente reformular a pergunta.",
            citations=[Citation(**c) for c in get_citations()],
            documented=True,
            agent_used=True,
        )


def get_agent() -> MaintenanceAgent | None:
    """Singleton lazy; None quando o ADK não está disponível (aciona fallback)."""
    global _agent_singleton
    if _agent_singleton is False:
        try:
            _agent_singleton = MaintenanceAgent()
            log.info("Agente ADK inicializado (modelo %s).", get_settings().gemini_chat_model)
        except Exception as exc:
            log.warning("Agente ADK indisponível (%s) — usando fallback RAG.", exc)
            _agent_singleton = None
    return _agent_singleton
