"""Testes de integração da API (TestClient + SQLite + FakeLLM + artefatos sintéticos)."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.core.schemas import FEATURE_COLUMNS


@pytest.fixture(scope="module")
def client(fake_llm, env_dirs, trained_artifacts):
    from src.api.deps import get_db, get_llm
    from src.api.main import app
    from src.core.db import Base

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    def override_db():
        session = factory()
        try:
            yield session
        finally:
            session.close()

    from src.api.deps import get_chat_agent

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_llm] = lambda: fake_llm
    app.dependency_overrides[get_chat_agent] = lambda: None  # testa o fluxo fallback
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _event_payload(value: float) -> dict:
    payload = {c: value for c in FEATURE_COLUMNS}
    payload.update({"id": 42, "created_at": "2026-06-01T00:00:00+00:00"})
    return payload


def test_health(client):
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["artifacts_loaded"] is True


def test_diagnose_fault_documented_flow(client, fake_llm, env_dirs):
    # indexa documento p/ cocked_rotor no Chroma de teste
    from src.rag import store
    from tests.test_rag_guardrail import _make_pdf

    store.add_document(
        pdf_bytes=_make_pdf(["1. Objetivo", "Procedimento de correcao do cocked rotor. " * 10]),
        filename="DocTeste.pdf",
        title="Cocked Rotor",
        families=["cocked_rotor"],
        llm=fake_llm,
    )

    response = client.post("/api/v1/diagnose", json=_event_payload(3.0))
    assert response.status_code == 200
    body = response.json()
    assert body["predicted_fault"] == "cocked_rotor"
    assert body["is_fault"] is True
    assert body["documented"] is True
    assert body["prescription"] is not None
    assert body["prescription"]["citations"]
    assert body["similar_events"]["count"] > 0
    assert 0 <= body["probability"] <= 1


def test_diagnose_fault_undocumented_flow(client):
    """ventoinha não tem documento → aviso + sugestão de registro (requisito do edital)."""
    response = client.post("/api/v1/diagnose", json=_event_payload(-3.0))
    assert response.status_code == 200
    body = response.json()
    assert body["predicted_fault"] == "ventoinha"
    assert body["is_fault"] is True
    assert body["documented"] is False
    assert body["prescription"] is None
    assert "registre um novo documento" in body["suggestion"]


def test_diagnose_normal_state(client):
    response = client.post("/api/v1/diagnose", json=_event_payload(0.0))
    assert response.status_code == 200
    body = response.json()
    assert body["predicted_fault"] == "normal"
    assert body["is_fault"] is False
    assert body["prescription"] is None


def test_diagnose_invalid_payload(client):
    response = client.post("/api/v1/diagnose", json={"foo": "bar"})
    assert response.status_code == 422


def test_chat_endpoint(client):
    response = client.post(
        "/api/v1/chat",
        json={"message": "como corrigir?", "fault_family": "cocked_rotor", "history": []},
    )
    assert response.status_code == 200
    assert response.json()["documented"] is True


def test_upload_document_invalid_family(client):
    response = client.post(
        "/api/v1/documents",
        files={"file": ("x.pdf", b"%PDF-fake", "application/pdf")},
        data={"title": "Doc", "families": "familia_inexistente"},
    )
    assert response.status_code == 422
