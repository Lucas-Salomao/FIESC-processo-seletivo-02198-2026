"""Agregações analíticas sobre o histórico (SQL sob demanda).

Funções puras `(engine) -> dict`, testáveis isoladamente e reutilizadas pelas
rotas /api/v1/analytics/*. Rodam no Postgres em ~0,15s sobre 166k eventos, por
isso não há artefato pré-computado: o dashboard reflete inclusive os eventos
que chegam por MQTT.

Nota de dialeto: usa `percentile_cont`, `stddev_samp` e `var_samp` — presentes
no PostgreSQL, ausentes no SQLite. Os testes destas funções sobem um schema
temporário no Postgres.
"""

import json
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import Engine

from src.core.config import get_settings
from src.core.schemas import FEATURE_COLUMNS

# Mínimo de eventos para uma sessão de coleta entrar na análise de vazamento.
MIN_SESSION_EVENTS = 30


def _safe_columns(columns: list[str]) -> list[str]:
    """Whitelist: só interpolamos nomes vindos da nossa própria constante."""
    invalid = set(columns) - set(FEATURE_COLUMNS)
    if invalid:
        raise ValueError(f"Colunas fora da whitelist: {sorted(invalid)}")
    return columns


def severity_by_rpm(engine: Engine) -> dict:
    """Distribuição da velocidade RMS por família × regime de RPM.

    Estratificar por RPM é obrigatório: o RMS mediano é quase idêntico entre
    famílias quando os regimes são misturados, o que torna qualquer
    comparação global de severidade enganosa.
    """
    query = text("""
        SELECT f.name AS family, f.is_fault, e.rpm, count(*) AS n,
               percentile_cont(0.5)  WITHIN GROUP (ORDER BY e.x_rms_velocity_mm_s) AS x_p50,
               percentile_cont(0.95) WITHIN GROUP (ORDER BY e.x_rms_velocity_mm_s) AS x_p95,
               percentile_cont(0.99) WITHIN GROUP (ORDER BY e.x_rms_velocity_mm_s) AS x_p99,
               percentile_cont(0.5)  WITHIN GROUP (ORDER BY e.z_rms_velocity_mm_s) AS z_p50,
               percentile_cont(0.95) WITHIN GROUP (ORDER BY e.z_rms_velocity_mm_s) AS z_p95,
               percentile_cont(0.5)  WITHIN GROUP (ORDER BY e.temperature_c)       AS temp_p50
        FROM events e
        JOIN fault_families f ON f.id = e.family_id
        WHERE e.rpm > 0 AND e.x_rms_velocity_mm_s IS NOT NULL
        GROUP BY f.name, f.is_fault, e.rpm
        HAVING count(*) >= 10
        ORDER BY f.name, e.rpm
    """)
    with engine.connect() as conn:
        rows = conn.execute(query).mappings().all()

    records = [
        {
            "family": r["family"],
            "is_fault": bool(r["is_fault"]),
            "rpm": float(r["rpm"]),
            "n": int(r["n"]),
            **{k: _round(r[k]) for k in ("x_p50", "x_p95", "x_p99", "z_p50", "z_p95", "temp_p50")},
        }
        for r in rows
    ]

    # Linha de base por RPM: mediana do estado `normal` no mesmo regime.
    baseline = {
        rec["rpm"]: rec["x_p50"] for rec in records if rec["family"] == "normal" and rec["x_p50"]
    }
    for rec in records:
        base = baseline.get(rec["rpm"])
        rec["baseline_x_p50"] = base
        rec["relative_severity"] = (
            round(rec["x_p50"] / base, 2) if base and rec["x_p50"] is not None else None
        )

    return {"records": records, "baseline_by_rpm": baseline}


def fault_signatures(engine: Engine) -> dict:
    """Assinatura de cada família (z-score médio por feature) e o poder
    discriminativo de cada feature (razão variância entre-famílias /
    dentro-família, no espírito de um F de ANOVA).

    É esta razão que mostra objetivamente quais indicadores separam as falhas
    e quais não separam — evitando conclusões só por inspeção visual.
    """
    cols = _safe_columns(list(FEATURE_COLUMNS))
    per_family_cols = ", ".join(f"avg({c}) AS m_{c}, var_samp({c}) AS v_{c}" for c in cols)
    global_cols = ", ".join(f"avg({c}) AS g_{c}, stddev_samp({c}) AS s_{c}" for c in cols)

    with engine.connect() as conn:
        families = (
            conn.execute(
                text(f"""
                SELECT f.name AS family, f.is_fault, count(*) AS n, {per_family_cols}
                FROM events e JOIN fault_families f ON f.id = e.family_id
                GROUP BY f.name, f.is_fault
                HAVING count(*) >= {MIN_SESSION_EVENTS}
                ORDER BY f.name
            """)
            )
            .mappings()
            .all()
        )
        overall = (
            conn.execute(
                text(f"""
                SELECT count(*) AS n, {global_cols}
                FROM events e WHERE e.family_id IS NOT NULL
            """)
            )
            .mappings()
            .one()
        )

    signatures, total_n = [], sum(int(r["n"]) for r in families)
    for row in families:
        z_scores = {}
        for col in cols:
            std = overall[f"s_{col}"]
            mean = row[f"m_{col}"]
            z_scores[col] = (
                round((float(mean) - float(overall[f"g_{col}"])) / float(std), 3)
                if std and mean is not None
                else None
            )
        signatures.append(
            {
                "family": row["family"],
                "is_fault": bool(row["is_fault"]),
                "n": int(row["n"]),
                "z_scores": z_scores,
            }
        )

    discriminative = []
    for col in cols:
        grand = float(overall[f"g_{col}"]) if overall[f"g_{col}"] is not None else None
        if grand is None or total_n <= len(families):
            continue
        between = sum(
            int(r["n"]) * (float(r[f"m_{col}"]) - grand) ** 2
            for r in families
            if r[f"m_{col}"] is not None
        ) / max(len(families) - 1, 1)
        within = sum((int(r["n"]) - 1) * float(r[f"v_{col}"] or 0.0) for r in families) / max(
            total_n - len(families), 1
        )
        discriminative.append(
            {"feature": col, "f_ratio": round(between / within, 2) if within > 0 else None}
        )
    discriminative.sort(key=lambda d: (d["f_ratio"] is None, -(d["f_ratio"] or 0)))

    return {"signatures": signatures, "discriminative_power": discriminative, "features": cols}


def leakage_evidence(engine: Engine) -> dict:
    """Quanto cada feature identifica a SESSÃO de coleta em vez da falha.

    Compara a dispersão ENTRE as médias das sessões (`raw_fault` identifica a
    campanha) com a dispersão DENTRO de cada sessão. Razão alta = a feature
    funciona como carimbo temporal da campanha, um vazamento que infla a
    métrica do split aleatório e desaba no holdout por sessão.
    """
    cols = _safe_columns(list(FEATURE_COLUMNS))
    inner = ", ".join(f"avg({c}) AS m_{c}, stddev_samp({c}) AS s_{c}" for c in cols)
    outer = ", ".join(
        f"stddev_samp(m_{c}) AS between_{c}, "
        f"percentile_cont(0.5) WITHIN GROUP (ORDER BY s_{c}) AS within_{c}"
        for c in cols
    )
    query = text(f"""
        WITH per_session AS (
            SELECT raw_fault, {inner}
            FROM events
            WHERE raw_fault IS NOT NULL
            GROUP BY raw_fault
            HAVING count(*) >= {MIN_SESSION_EVENTS}
        )
        SELECT count(*) AS n_sessions, {outer} FROM per_session
    """)
    with engine.connect() as conn:
        row = conn.execute(query).mappings().one()

    features = []
    for col in cols:
        between, within = row[f"between_{col}"], row[f"within_{col}"]
        if between is None or within is None:
            continue
        between, within = float(between), float(within)
        features.append(
            {
                "feature": col,
                "between_sessions_sd": round(between, 3),
                "within_session_sd": round(within, 3),
                "ratio": round(between / within, 1) if within > 0 else None,
            }
        )
    features.sort(key=lambda f: (f["ratio"] is None, -(f["ratio"] or 0)))
    return {"n_sessions": int(row["n_sessions"]), "features": features}


def model_quality() -> dict:
    """Métricas do treino + importância das features do LightGBM."""
    artifacts = get_settings().artifacts_dir
    metrics = _read_metrics(artifacts / "metrics.json")
    return {"metrics": metrics, "feature_importance": _read_importances(artifacts / "lgbm.joblib")}


def _read_metrics(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _read_importances(path: Path) -> list[dict]:
    if not path.exists():
        return []
    import joblib

    from src.ml.features import MODEL_COLUMNS

    clf = joblib.load(path)
    raw = [float(v) for v in clf.feature_importances_]
    total = sum(raw) or 1.0
    pairs = [
        {"feature": name, "importance_pct": round(value / total * 100, 2)}
        for name, value in zip(MODEL_COLUMNS, raw, strict=True)
    ]
    pairs.sort(key=lambda p: -p["importance_pct"])
    return pairs


def _round(value, digits: int = 3):
    return round(float(value), digits) if value is not None else None
