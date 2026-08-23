"""Ingestão inicial da base documental.

Em desenvolvimento a indexação é feita à mão por `scripts/ingest_docs.py`. Em
deploy o índice vive em um volume que nasce vazio, e o operador não tem shell
dentro do container — por isso a API sabe se indexar sozinha no primeiro boot.
"""

import logging
import threading
from pathlib import Path

import yaml

from src.core.config import get_settings
from src.rag import store

log = logging.getLogger("rag.bootstrap")

COVERAGE_PATH = Path(__file__).parent / "coverage.yaml"


def ingest_initial_documents(docs_dir: Path, llm=None) -> dict[str, int]:
    """Indexa os documentos declarados em coverage.yaml.

    Idempotente: `store.add_document` remove os chunks anteriores do mesmo
    arquivo antes de reindexar.
    """
    from src.llm.client import get_llm_client

    coverage = yaml.safe_load(COVERAGE_PATH.read_text(encoding="utf-8"))
    llm = llm or get_llm_client()

    indexed: dict[str, int] = {}
    for filename, cfg in coverage.items():
        pdf_path = docs_dir / filename
        if not pdf_path.exists():
            log.warning("%s não encontrado em %s — pulando.", filename, docs_dir)
            continue
        indexed[filename] = store.add_document(
            pdf_bytes=pdf_path.read_bytes(),
            filename=filename,
            title=cfg["title"],
            families=cfg["families"],
            llm=llm,
        )
        log.info("%s: %s chunks indexados.", filename, indexed[filename])
    return indexed


def bootstrap_if_empty() -> None:
    """Dispara a ingestão em segundo plano quando o índice está vazio.

    A thread separada é proposital: a ingestão faz dezenas de chamadas ao
    Vertex AI (incluindo o OCR do Doc1, que é escaneado) e leva minutos.
    Bloquear o startup faria o healthcheck do orquestrador reprovar a instância
    antes de a API ficar pronta para responder.
    """
    settings = get_settings()
    if not settings.rag_bootstrap:
        return

    try:
        if store.documented_families():
            log.info("Base documental já indexada — bootstrap dispensado.")
            return
    except Exception as exc:  # índice corrompido ou indisponível
        log.warning("Não foi possível inspecionar o índice (%s) — bootstrap cancelado.", exc)
        return

    def _run() -> None:
        try:
            log.info("Índice vazio: iniciando ingestão da base documental.")
            indexed = ingest_initial_documents(settings.docs_dir)
            log.info("Bootstrap concluído: %s", indexed or "nenhum documento encontrado")
        except Exception:
            log.exception("Bootstrap da base documental falhou — use POST /documents.")

    threading.Thread(target=_run, name="rag-bootstrap", daemon=True).start()
