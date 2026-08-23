"""ETL: banner.csv → PostgreSQL (events, fault_families, label_map).

Uso:
    python scripts/ingest_data.py [--csv documentos/banner.csv]

Etapas:
1. Lê o CSV e valida tipos.
2. Canonicaliza TODOS os rótulos de `fault` (falha se algum for desconhecido).
3. Popula fault_families, label_map e events (idempotente: trunca e recarrega).
"""

import argparse
import sys
from pathlib import Path

import pandas as pd
from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.core.db import create_all, get_engine  # noqa: E402
from src.core.schemas import FEATURE_COLUMNS  # noqa: E402
from src.etl.canonize import get_canonizer  # noqa: E402


def main(csv_path: Path) -> None:
    """Lê o banner.csv, resolve as famílias canônicas de todos os rótulos e
    recarrega as tabelas fault_families, label_map e events no Postgres."""
    canonizer = get_canonizer()

    print(f"Lendo {csv_path} ...")
    df = pd.read_csv(csv_path, parse_dates=["created_at"])
    print(f"{len(df):,} registros lidos.")

    # --- Canonicalização (audita 100% dos rótulos antes de gravar qualquer coisa)
    raw_labels = df["fault"].astype(str).str.strip().str.lower()
    unique_labels = sorted(raw_labels.unique())
    mapping = {lbl: canonizer.canonize(lbl) for lbl in unique_labels}  # raises se desconhecido
    print(
        f"{len(unique_labels)} rótulos brutos mapeados para "
        f"{len({f.name for f in mapping.values()})} famílias canônicas."
    )

    df["family"] = raw_labels.map({k: v.name for k, v in mapping.items()})

    # --- Limpeza mínima
    before = len(df)
    df = df.drop_duplicates(subset=["id"]).dropna(subset=FEATURE_COLUMNS)
    if len(df) != before:
        print(f"Removidos {before - len(df)} registros duplicados/incompletos.")

    # --- Gravação
    engine = get_engine()
    create_all()

    families = sorted(canonizer.families.values(), key=lambda f: f.name)
    family_ids = {f.name: i + 1 for i, f in enumerate(families)}

    with engine.begin() as conn:
        conn.execute(
            text(
                "TRUNCATE diagnoses, doc_coverage, events, label_map, fault_families "
                "RESTART IDENTITY CASCADE"
            )
        )
        conn.execute(
            text("INSERT INTO fault_families (id, name, is_fault) VALUES (:id, :name, :is_fault)"),
            [{"id": family_ids[f.name], "name": f.name, "is_fault": f.is_fault} for f in families],
        )
        conn.execute(
            text("INSERT INTO label_map (raw_label, family_id) VALUES (:raw, :fid)"),
            [{"raw": lbl, "fid": family_ids[fam.name]} for lbl, fam in mapping.items()],
        )

    events = df[["id", "created_at", "fault", "family", *FEATURE_COLUMNS]].copy()
    events = events.rename(columns={"fault": "raw_fault"})
    events["family_id"] = events.pop("family").map(family_ids)

    print("Gravando eventos no Postgres (chunks de 10k)...")
    events.to_sql("events", engine, if_exists="append", index=False, chunksize=10_000)

    with engine.connect() as conn:
        count = conn.execute(text("SELECT count(*) FROM events")).scalar()
    print(f"OK — {count:,} eventos no banco.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", type=Path, default=Path("documentos/banner.csv"))
    args = parser.parse_args()
    main(args.csv)
