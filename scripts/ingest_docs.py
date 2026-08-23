"""Ingestão da base documental inicial (Doc1..Doc6) no ChromaDB.

Uso:
    python scripts/ingest_docs.py [--docs-dir documentos]

Requer credenciais do Vertex AI configuradas (.env) — os embeddings são
gerados com gemini-embedding-2.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.rag import store  # noqa: E402
from src.rag.bootstrap import ingest_initial_documents  # noqa: E402


def main(docs_dir: Path) -> None:
    indexed = ingest_initial_documents(docs_dir)
    for filename, chunks in indexed.items():
        print(f"{filename}: {chunks} chunks indexados")
    print("Famílias documentadas:", sorted(store.documented_families()))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--docs-dir", type=Path, default=Path("documentos"))
    args = parser.parse_args()
    main(args.docs_dir)
