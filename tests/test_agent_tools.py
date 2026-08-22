"""Ferramentas do agente ADK — testes 100% offline.

As tools são funções puras: testadas com SQLite em arquivo, Chroma de teste
e FakeLLM. O loop real do ADK (LlmAgent/Runner) exige Gemini e não roda no CI;
o wiring do endpoint é testado com um FakeAgent injetado via dependency override.
"""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from src.core.schemas import ChatResponse


# ---------------------------------------------------------------------------
# consultar_historico (SQLite em arquivo, com seed)
# ---------------------------------------------------------------------------
@pytest.fixture()
def seeded_db(tmp_path, env_dirs, monkeypatch):
    import os

    from src.core.config import get_settings

    db_path = tmp_path / "hist.db"
    monkeypatch.setitem(os.environ, "DATABASE_URL", f"sqlite:///{db_path}")
    get_settings.cache_clear()

    from src.core.db import Event, FaultFamily, create_all, get_session_factory

    create_all()
    base = datetime(2026, 6, 15, tzinfo=UTC)
    with get_session_factory()() as session:
        session.add(FaultFamily(id=1, name="cocked_rotor", is_fault=True))
        session.add(FaultFamily(id=2, name="normal", is_fault=False))
        for i in range(10):
            session.add(
                Event(
                    id=i + 1,
                    created_at=base - timedelta(days=i * 5),
                    raw_fault="cocked_rotor",
                    family_id=1,
                    rpm=1000.0,
                )
            )
        session.add(Event(id=99, created_at=base, raw_fault="normal", family_id=2, rpm=0.0))
        session.commit()

    yield

    get_settings.cache_clear()


def test_consultar_historico_geral(seeded_db):
    from src.llm.agent_tools import consultar_historico

    result = consultar_historico(dias=30)
    familias = {r["familia"]: r["quantidade"] for r in result["ocorrencias_na_janela"]}
    # janela inclusiva de 30 dias a partir do mais recente: dias 0,5,...,30 → 7 eventos
    assert familias["cocked_rotor"] == 7
    assert familias["normal"] == 1


def test_consultar_historico_familia_com_detalhe(seeded_db):
    from src.llm.agent_tools import consultar_historico

    result = consultar_historico(familia="cocked_rotor", dias=365)
    assert result["detalhe_familia"]["total_historico"] == 10
    assert result["detalhe_familia"]["frequencia_por_dia"] > 0


def test_consultar_historico_familia_invalida(seeded_db):
    from src.llm.agent_tools import consultar_historico

    result = consultar_historico(familia="turbina_warp")
    assert "erro" in result
    assert "cocked_rotor" in result["familias_validas"]


# ---------------------------------------------------------------------------
# buscar_documentos (guardrail + citações determinísticas)
# ---------------------------------------------------------------------------
@pytest.fixture()
def correia_doc(fake_llm, env_dirs, monkeypatch):
    import src.llm.agent_tools as agent_tools
    from src.rag import store
    from tests.test_rag_guardrail import _make_pdf

    monkeypatch.setattr(agent_tools, "get_llm_client", lambda: fake_llm)
    store.add_document(
        pdf_bytes=_make_pdf(["1. Objetivo", "Procedimento de correcao de correias. " * 10]),
        filename="DocCorreia.pdf",
        title="Correias",
        families=["correia"],
        llm=fake_llm,
    )


def test_buscar_documentos_documentado(correia_doc):
    from src.llm.agent_tools import buscar_documentos, get_citations, reset_citations

    reset_citations()
    result = buscar_documentos("como corrigir correia frouxa", familia="correia")
    assert result["documentado"] is True
    assert result["trechos"]
    assert result["trechos"][0]["fonte"]["doc"] == "DocCorreia.pdf"
    # citações registradas deterministicamente pelo retrieval
    citations = get_citations()
    assert citations and citations[0]["doc"] == "DocCorreia.pdf"


def test_buscar_documentos_guardrail_nao_documentado(correia_doc):
    from src.llm.agent_tools import buscar_documentos

    result = buscar_documentos("como corrigir", familia="falta_fase")
    assert result["documentado"] is False
    assert "não existe documento" in result["aviso"]


def test_buscar_documentos_familia_invalida(correia_doc):
    from src.llm.agent_tools import buscar_documentos

    assert "erro" in buscar_documentos("x", familia="inexistente")


# ---------------------------------------------------------------------------
# diagnosticar_evento (artefatos sintéticos do conftest)
# ---------------------------------------------------------------------------
def test_diagnosticar_evento(trained_artifacts, sample_event):
    from src.llm.agent_tools import diagnosticar_evento

    result = diagnosticar_evento(sample_event.model_dump_json())
    assert result["familia_prevista"] == "cocked_rotor"
    assert result["eh_falha"] is True
    assert 0 <= result["probabilidade"] <= 1
    assert result["ocorrencias_similares"]["total"] > 0


def test_diagnosticar_evento_json_invalido(trained_artifacts):
    from src.llm.agent_tools import diagnosticar_evento

    result = diagnosticar_evento('{"foo": 1}')
    assert result["erro"] == "JSON de evento inválido."
    assert result["detalhes"]


# ---------------------------------------------------------------------------
# cobertura_documental
# ---------------------------------------------------------------------------
def test_cobertura_documental(correia_doc, seeded_db):
    from src.llm.agent_tools import cobertura_documental

    result = cobertura_documental()
    assert "correia" in result["documentadas"]
    assert "falta_fase" in result["sem_documento"]


# ---------------------------------------------------------------------------
# Wiring do endpoint /chat: agente vs fallback
# ---------------------------------------------------------------------------
class FakeAgent:
    async def ask(self, message, session_id=None, user_id="ui"):
        return ChatResponse(answer_md=f"eco do agente: {message}", agent_used=True)


@pytest.fixture()
def api_client(fake_llm, env_dirs):
    from src.api.deps import get_llm
    from src.api.main import app

    app.dependency_overrides[get_llm] = lambda: fake_llm
    with TestClient(app) as client:
        yield client, app
    app.dependency_overrides.clear()


def test_chat_usa_agente_quando_disponivel(api_client):
    from src.api.deps import get_chat_agent

    client, app = api_client
    app.dependency_overrides[get_chat_agent] = lambda: FakeAgent()
    response = client.post("/api/v1/chat", json={"message": "olá", "session_id": "s1"})
    assert response.status_code == 200
    body = response.json()
    assert body["agent_used"] is True
    assert "eco do agente" in body["answer_md"]


def test_chat_fallback_sem_agente(api_client, correia_doc):
    from src.api.deps import get_chat_agent

    client, app = api_client
    app.dependency_overrides[get_chat_agent] = lambda: None
    response = client.post(
        "/api/v1/chat", json={"message": "como corrigir?", "fault_family": "correia"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["agent_used"] is False
    assert body["documented"] is True
