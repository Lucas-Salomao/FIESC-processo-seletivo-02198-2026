"""Camada de acesso ao ChromaDB — indexação e retrieval dos documentos.

Fonte de verdade da cobertura documental em runtime: uma família de falha é
considerada DOCUMENTADA se possui ao menos um chunk indexado. O guardrail
(`is_documented`) é determinístico — nunca decidido pelo LLM.
"""

from functools import lru_cache

import chromadb

from src.core.config import get_settings
from src.llm.client import LLMClient
from src.rag.chunking import chunk_pdf

COLLECTION = "manuals"


@lru_cache
def _client() -> chromadb.ClientAPI:
    settings = get_settings()
    settings.chroma_dir.mkdir(parents=True, exist_ok=True)
    return chromadb.PersistentClient(path=str(settings.chroma_dir))


def get_collection():
    return _client().get_or_create_collection(COLLECTION, metadata={"hnsw:space": "cosine"})


def add_document(
    pdf_bytes: bytes,
    filename: str,
    title: str,
    families: list[str],
    llm: LLMClient,
) -> int:
    """Ingesta (ou re-ingesta) um documento; um chunk por família coberta."""
    collection = get_collection()
    collection.delete(where={"doc": filename})  # idempotente

    chunks = chunk_pdf(pdf_bytes, doc_name=filename, title=title, transcribe=llm.transcribe)
    if not chunks:
        return 0

    texts = [c.text for c in chunks]
    embeddings = llm.embed(texts, task_type="RETRIEVAL_DOCUMENT")

    ids, docs, metas, embs = [], [], [], []
    for family in families:
        for i, (chunk, emb) in enumerate(zip(chunks, embeddings, strict=True)):
            ids.append(f"{filename}::{family}::{i}")
            docs.append(chunk.text)
            embs.append(emb)
            metas.append(
                {
                    "doc": chunk.doc,
                    "title": chunk.title,
                    "section": chunk.section,
                    "page": chunk.page,
                    "family": family,
                }
            )
    collection.add(ids=ids, documents=docs, metadatas=metas, embeddings=embs)
    return len(chunks)


def is_documented(family: str) -> bool:
    result = get_collection().get(where={"family": family}, limit=1)
    return len(result["ids"]) > 0


def documented_families() -> set[str]:
    result = get_collection().get(include=["metadatas"])
    return {m["family"] for m in result["metadatas"]}


def retrieve(query: str, family: str, llm: LLMClient, top_k: int | None = None) -> list[dict]:
    settings = get_settings()
    k = top_k or settings.rag_top_k
    query_emb = llm.embed([query], task_type="RETRIEVAL_QUERY")[0]
    result = get_collection().query(
        query_embeddings=[query_emb],
        n_results=k,
        where={"family": family},
        include=["documents", "metadatas", "distances"],
    )
    return [
        {"text": doc, "metadata": meta, "distance": dist}
        for doc, meta, dist in zip(
            result["documents"][0], result["metadatas"][0], result["distances"][0], strict=False
        )
    ]
