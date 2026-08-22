"""Ingestão da base documental inicial (Doc1..Doc6) no ChromaDB.

Uso:
    python scripts/ingest_docs.py [--docs-dir documentos]

Requer credenciais do Vertex AI configuradas (.env) — os embeddings são
gerados com gemini-embedding-2.
"""

import argparse
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.llm.client import get_llm_client  # noqa: E402
from src.rag import store  # noqa: E402

COVERAGE_PATH = Path(__file__).parent.parent / "src" / "rag" / "coverage.yaml"


def main(docs_dir: Path) -> None:
    coverage = yaml.safe_load(COVERAGE_PATH.read_text(encoding="utf-8"))
    llm = get_llm_client()

    for filename, cfg in coverage.items():
        pdf_path = docs_dir / filename
        if not pdf_path.exists():
            print(f"AVISO: {pdf_path} não encontrado — pulando.")
            continue
        n = store.add_document(
            pdf_bytes=pdf_path.read_bytes(),
            filename=filename,
            title=cfg["title"],
            families=cfg["families"],
            llm=llm,
        )
        print(f"{filename}: {n} chunks indexados p/ famílias {cfg['families']}")

    print("Famílias documentadas:", sorted(store.documented_families()))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--docs-dir", type=Path, default=Path("documentos"))
    args = parser.parse_args()
    main(args.docs_dir)
