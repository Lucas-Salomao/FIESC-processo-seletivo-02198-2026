"""Agregações analíticas — testadas contra PostgreSQL em schema temporário.

As consultas usam `percentile_cont`, `stddev_samp` e `var_samp`, que não
existem no SQLite usado pelo restante da suíte. Em vez de enfraquecer o SQL
para caber no SQLite, estes testes sobem um schema descartável no Postgres
(o CI já provisiona um service container) e são pulados quando não houver
banco alcançável.
"""

import os
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError

from src.api import analytics

PG_URL = os.getenv("PG_TEST_URL", "postgresql+psycopg2://fiesc:fiesc@localhost:5432/fiesc")
SCHEMA = "analytics_test"


def _pg_engine():
    try:
        engine = create_engine(PG_URL, connect_args={"options": f"-csearch_path={SCHEMA}"})
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return engine
    except SQLAlchemyError:
        return None


@pytest.fixture(scope="module")
def seeded_pg():
    """Schema temporário com duas famílias de perfis conhecidos e distintos."""
    admin = create_engine(PG_URL)
    try:
        with admin.connect() as conn:
            conn.execute(text("SELECT 1"))
    except SQLAlchemyError:
        pytest.skip("PostgreSQL indisponível — testes de analytics pulados.")

    with admin.begin() as conn:
        conn.execute(text(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE"))
        conn.execute(text(f"CREATE SCHEMA {SCHEMA}"))

    engine = _pg_engine()
    from src.core.db import Base

    Base.metadata.create_all(engine)

    base_ts = datetime(2026, 6, 1, tzinfo=UTC)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO fault_families (id, name, is_fault) VALUES "
                "(1, 'desbalanceamento', true), (2, 'normal', false)"
            )
        )
        rows = []
        eid = 0
        # 'normal' estável em 2.0 mm/s; desbalanceamento sobe com a rotação.
        for rpm, desb_rms in ((1000.0, 2.4), (3000.0, 7.5)):
            for i in range(30):
                eid += 1
                rows.append(
                    {
                        "id": eid,
                        "ts": base_ts + timedelta(minutes=eid),
                        "raw": "normal",
                        "fid": 2,
                        "rpm": rpm,
                        "rms": 2.0 + (i % 3) * 0.01,
                        "temp": 20.0,
                        "kurt": 3.0 + (i % 5) * 0.1,
                    }
                )
                eid += 1
                rows.append(
                    {
                        "id": eid,
                        "ts": base_ts + timedelta(minutes=eid),
                        "raw": "desbalanceado_1",
                        "fid": 1,
                        "rpm": rpm,
                        "rms": desb_rms + (i % 3) * 0.01,
                        "temp": 30.0,
                        "kurt": 3.0 + (i % 5) * 0.1,
                    }
                )
        conn.execute(
            text("""
                INSERT INTO events (id, created_at, raw_fault, family_id, rpm,
                                    x_rms_velocity_mm_s, z_rms_velocity_mm_s,
                                    temperature_c, x_kurtosis)
                VALUES (:id, :ts, :raw, :fid, :rpm, :rms, :rms, :temp, :kurt)
            """),
            rows,
        )

    yield engine

    engine.dispose()
    with admin.begin() as conn:
        conn.execute(text(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE"))


def test_severity_estratificada_por_rpm(seeded_pg):
    result = analytics.severity_by_rpm(seeded_pg)
    by_key = {(r["family"], r["rpm"]): r for r in result["records"]}

    assert by_key[("desbalanceamento", 1000.0)]["x_p50"] == pytest.approx(2.41, abs=0.05)
    assert by_key[("desbalanceamento", 3000.0)]["x_p50"] == pytest.approx(7.51, abs=0.05)
    # baseline vem do estado `normal` do MESMO regime
    assert result["baseline_by_rpm"][1000.0] == pytest.approx(2.01, abs=0.05)


def test_severidade_relativa_usa_baseline_do_mesmo_rpm(seeded_pg):
    result = analytics.severity_by_rpm(seeded_pg)
    by_key = {(r["family"], r["rpm"]): r for r in result["records"]}
    # a 3000 rpm o desbalanceamento é ~3.7x a linha de base; a 1000 rpm, ~1.2x
    assert by_key[("desbalanceamento", 3000.0)]["relative_severity"] > 3.0
    assert by_key[("desbalanceamento", 1000.0)]["relative_severity"] < 1.5


def test_assinaturas_z_score_e_poder_discriminativo(seeded_pg):
    result = analytics.fault_signatures(seeded_pg)
    families = {s["family"]: s for s in result["signatures"]}
    assert set(families) == {"desbalanceamento", "normal"}
    # desbalanceamento está acima da média global de RMS; normal, abaixo
    assert families["desbalanceamento"]["z_scores"]["x_rms_velocity_mm_s"] > 0
    assert families["normal"]["z_scores"]["x_rms_velocity_mm_s"] < 0

    power = {d["feature"]: d["f_ratio"] for d in result["discriminative_power"]}
    # curtose tem a MESMA distribuição nas duas famílias => não discrimina
    assert power["x_rms_velocity_mm_s"] > 100 * power["x_kurtosis"]


def test_vazamento_detecta_feature_carimbo_de_sessao(seeded_pg):
    result = analytics.leakage_evidence(seeded_pg)
    ratios = {f["feature"]: f["ratio"] for f in result["features"]}
    assert result["n_sessions"] == 2
    # temperatura é constante DENTRO da sessão e difere ENTRE sessões (20 vs 30)
    assert ratios["temperature_c"] is None or ratios["temperature_c"] > 10


def test_colunas_fora_da_whitelist_sao_rejeitadas():
    with pytest.raises(ValueError, match="whitelist"):
        analytics._safe_columns(["x_rms_velocity_mm_s", "DROP TABLE events"])
