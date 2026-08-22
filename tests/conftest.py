"""Fixtures compartilhadas — nenhum teste chama Vertex AI ou exige Postgres.

- LLM: FakeLLM determinístico (embeddings por hash, geração canned).
- Banco: SQLite em memória via override de dependência.
- Artefatos ML: treinados sobre um CSV sintético minúsculo (também cobre o
  pipeline de treino de ponta a ponta).
"""

import hashlib
import struct

import numpy as np
import pandas as pd
import pytest

FAKE_DIM = 64


class FakeLLM:
    """Mesma interface de src.llm.client.LLMClient, 100% offline."""

    def __init__(self) -> None:
        self.generate_calls: list[str] = []

    def generate(self, prompt: str, system: str | None = None) -> str:
        self.generate_calls.append(prompt)
        return (
            "### Instruções de correção\n1. Desligar o equipamento [1].\n2. Corrigir a falha [2]."
        )

    def transcribe(self, image_png: bytes) -> str:
        return (
            "1. Objetivo\n\nTexto transcrito por OCR do procedimento tecnico de manutencao "
            "para validacao do fallback de PDFs escaneados sem camada de texto."
        )

    def embed(self, texts: list[str], task_type: str = "RETRIEVAL_DOCUMENT") -> list[list[float]]:
        out = []
        for text in texts:
            digest = hashlib.sha256(text.encode()).digest()
            buf = (digest * (FAKE_DIM * 2 // len(digest) + 1))[: FAKE_DIM * 2]
            vec = np.array(struct.unpack(f"<{FAKE_DIM}H", buf), dtype=float)
            out.append((vec / (np.linalg.norm(vec) or 1.0)).tolist())
        return out


@pytest.fixture(scope="session")
def fake_llm() -> FakeLLM:
    return FakeLLM()


@pytest.fixture(scope="session")
def env_dirs(tmp_path_factory):
    """Aponta artefatos e Chroma para diretórios temporários ANTES dos imports com cache."""
    base = tmp_path_factory.mktemp("fiesc")
    artifacts = base / "artifacts"
    chroma = base / "chroma"
    artifacts.mkdir()
    chroma.mkdir()

    import os

    os.environ["ARTIFACTS_DIR"] = str(artifacts)
    os.environ["CHROMA_DIR"] = str(chroma)
    os.environ["DATABASE_URL"] = "sqlite://"
    # Garante que o agente ADK real NUNCA inicializa nos testes,
    # mesmo com um .env local configurado (env var vence o .env)
    os.environ["GOOGLE_CLOUD_PROJECT"] = ""

    from src.core.config import get_settings

    get_settings.cache_clear()
    return {"artifacts": artifacts, "chroma": chroma}


@pytest.fixture(scope="session")
def synthetic_csv(tmp_path_factory, env_dirs):
    """Dataset sintético: 3 famílias x 2 sessões, distribuições separáveis."""
    from src.core.schemas import FEATURE_COLUMNS

    rng = np.random.default_rng(0)
    rows = []
    centers = {"cocked_rotor": 3.0, "normal": 0.0, "ventoinha": -3.0}
    idx = 0
    for base_label, center in centers.items():
        for session_suffix in ("", "_2"):
            for _ in range(120):
                idx += 1
                features = rng.normal(loc=center, scale=0.3, size=len(FEATURE_COLUMNS))
                row = {
                    "id": idx,
                    "created_at": f"2026-05-{rng.integers(1, 28):02d} 12:00:00+00:00",
                    "fault": f"{base_label}{session_suffix}",
                }
                row.update(dict(zip(FEATURE_COLUMNS, features, strict=False)))
                rows.append(row)

    csv_path = tmp_path_factory.mktemp("data") / "mini_banner.csv"
    pd.DataFrame(rows).to_csv(csv_path, index=False)
    return csv_path


@pytest.fixture(scope="session")
def trained_artifacts(synthetic_csv, env_dirs):
    from src.ml.train import train

    metrics = train(synthetic_csv, env_dirs["artifacts"])

    from src.ml.diagnose import get_engine_singleton

    get_engine_singleton.cache_clear()
    return metrics


@pytest.fixture()
def sample_event():
    from src.core.schemas import FEATURE_COLUMNS, SensorEvent

    # Próximo do centro da família cocked_rotor do dataset sintético (3.0)
    return SensorEvent(
        id=999_999,
        created_at="2026-06-01T00:00:00+00:00",
        **{c: 3.0 for c in FEATURE_COLUMNS},
    )
