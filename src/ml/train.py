"""Treino do motor de diagnóstico: StandardScaler + NearestNeighbors + LightGBM.

Uso:
    python -m src.ml.train [--csv documentos/banner.csv] [--out artifacts]

## Estratégia de validação (justificativa)

O dataset é composto por SESSÕES de coleta (o rótulo bruto identifica a sessão:
`rolamento_inner_2`, `new_rolamento_inner_0`, `desalinhado_2`...). Leituras da
mesma sessão são quase idênticas, portanto:

- split ALEATÓRIO vaza near-duplicatas entre treino e teste → métrica inflada;
- split TEMPORAL puro é impossível aqui: 4 famílias (ex.: desalinhamento,
  falta_fase) só existem no período final do histórico.

Avaliação adotada: **holdout por sessão** — para cada família, ~20% das sessões
(rótulos brutos) inteiras vão para o teste. Mede exatamente a pergunta de
produção: "o modelo reconhece uma falha conhecida em uma NOVA campanha de
coleta?". Para referência, a métrica do split aleatório também é registrada.

O modelo FINAL (artefato servido) é retreinado com 100% dos dados.
"""

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from lightgbm import LGBMClassifier
from sklearn.metrics import accuracy_score, f1_score
from sklearn.model_selection import train_test_split
from sklearn.neighbors import NearestNeighbors
from sklearn.preprocessing import StandardScaler

from src.core.schemas import FEATURE_COLUMNS
from src.etl.canonize import get_canonizer
from src.ml.features import MODEL_COLUMNS, feature_matrix

KNN_NEIGHBORS = 25
SESSION_TEST_FRACTION = 0.2
RANDOM_STATE = 42


def load_dataset(csv_path: Path) -> pd.DataFrame:
    canonizer = get_canonizer()
    df = pd.read_csv(csv_path, parse_dates=["created_at"])
    raw = df["fault"].astype(str).str.strip().str.lower()
    mapping = {lbl: canonizer.canonize(lbl).name for lbl in raw.unique()}
    df["session"] = raw
    df["family"] = raw.map(mapping)
    df = df.drop_duplicates(subset=["id"]).dropna(subset=FEATURE_COLUMNS)
    return df.sort_values("created_at").reset_index(drop=True)


def session_holdout_mask(df: pd.DataFrame, rng: np.random.Generator) -> np.ndarray:
    """True = teste. Segura ~20% das sessões de cada família (mínimo 1, se houver >=2)."""
    test_sessions: set[str] = set()
    for _, group in df.groupby("family"):
        sessions = sorted(group["session"].unique())
        if len(sessions) < 2:
            continue  # família com sessão única fica inteira no treino
        n_test = max(1, round(len(sessions) * SESSION_TEST_FRACTION))
        test_sessions.update(rng.choice(sessions, size=n_test, replace=False))
    return df["session"].isin(test_sessions).to_numpy()


def _fit_lgbm(x_train: np.ndarray, y_train: np.ndarray) -> LGBMClassifier:
    clf = LGBMClassifier(
        n_estimators=400,
        learning_rate=0.08,
        num_leaves=63,
        subsample=0.9,
        colsample_bytree=0.9,
        class_weight="balanced",
        random_state=RANDOM_STATE,
        verbose=-1,
    )
    clf.fit(x_train, y_train)
    return clf


def _evaluate(y_true: np.ndarray, y_pred: np.ndarray) -> dict:
    labels_present = sorted(set(y_true))
    per_class = f1_score(y_true, y_pred, average=None, labels=labels_present)
    return {
        "accuracy": float(accuracy_score(y_true, y_pred)),
        "f1_macro": float(f1_score(y_true, y_pred, average="macro")),
        "f1_weighted": float(f1_score(y_true, y_pred, average="weighted")),
        "n_test": int(len(y_true)),
        "f1_per_class": {c: float(v) for c, v in zip(labels_present, per_class, strict=False)},
    }


def train(csv_path: Path, out_dir: Path) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    df = load_dataset(csv_path)
    x = feature_matrix(df)
    y = df["family"].to_numpy()
    classes = sorted(set(y))
    rng = np.random.default_rng(RANDOM_STATE)

    # --- Avaliação 1: holdout por sessão (métrica principal, honesta)
    test_mask = session_holdout_mask(df, rng)
    scaler_eval = StandardScaler().fit(x[~test_mask])
    clf_eval = _fit_lgbm(scaler_eval.transform(x[~test_mask]), y[~test_mask])
    y_pred = clf_eval.predict(scaler_eval.transform(x[test_mask]))
    eval_session = _evaluate(y[test_mask], y_pred)

    # --- Avaliação 2: split aleatório estratificado (referência otimista)
    x_tr, x_te, y_tr, y_te = train_test_split(
        x, y, test_size=0.2, stratify=y, random_state=RANDOM_STATE
    )
    scaler_rand = StandardScaler().fit(x_tr)
    clf_rand = _fit_lgbm(scaler_rand.transform(x_tr), y_tr)
    eval_random = _evaluate(y_te, clf_rand.predict(scaler_rand.transform(x_te)))

    # --- Avaliação 3: holdout por sessão SEM a temperatura absoluta.
    # A temperatura é a feature mais influente do modelo, mas varia ~9x mais
    # ENTRE campanhas de coleta do que dentro delas: funciona como carimbo da
    # sessão. Como cada sessão carrega um rótulo só, isso infla o split
    # aleatório. Este teste isola o efeito removendo-a do vetor de entrada.
    temp_idx = MODEL_COLUMNS.index("temperature_c")
    keep = [i for i in range(len(MODEL_COLUMNS)) if i != temp_idx]
    scaler_nt = StandardScaler().fit(x[~test_mask][:, keep])
    clf_nt = _fit_lgbm(scaler_nt.transform(x[~test_mask][:, keep]), y[~test_mask])
    eval_no_temp = _evaluate(
        y[test_mask], clf_nt.predict(scaler_nt.transform(x[test_mask][:, keep]))
    )

    # --- Modelo final servido: 100% dos dados
    scaler = StandardScaler().fit(x)
    clf = _fit_lgbm(scaler.transform(x), y)
    knn = NearestNeighbors(n_neighbors=KNN_NEIGHBORS, metric="euclidean").fit(scaler.transform(x))
    index_meta = df[["id", "created_at", "family"]].copy()

    metrics = {
        "trained_at": datetime.now(UTC).isoformat(),
        "n_total": int(len(df)),
        "classes": classes,
        "features": MODEL_COLUMNS,
        "knn_neighbors": KNN_NEIGHBORS,
        "eval_session_holdout": eval_session,
        "eval_random_split": eval_random,
        "eval_session_holdout_no_temp": eval_no_temp,
        "note": (
            "Métrica principal: eval_session_holdout (sessões inteiras de coleta "
            "no teste — mede generalização p/ novas campanhas). O split aleatório "
            "é referência otimista (vaza near-duplicatas da mesma sessão)."
        ),
    }

    joblib.dump(scaler, out_dir / "scaler.joblib")
    joblib.dump(clf, out_dir / "lgbm.joblib")
    joblib.dump(knn, out_dir / "knn.joblib")
    joblib.dump(index_meta, out_dir / "index_meta.joblib")
    (out_dir / "metrics.json").write_text(
        json.dumps(metrics, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    summary = {
        "session_holdout": {k: v for k, v in eval_session.items() if k != "f1_per_class"},
        "random_split": {k: v for k, v in eval_random.items() if k != "f1_per_class"},
        "session_holdout_no_temp": {k: v for k, v in eval_no_temp.items() if k != "f1_per_class"},
    }
    print(json.dumps(summary, indent=2))
    print("Artefatos gravados em", out_dir)
    return metrics


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", type=Path, default=Path("documentos/banner.csv"))
    parser.add_argument("--out", type=Path, default=Path("artifacts"))
    args = parser.parse_args()
    train(args.csv, args.out)
