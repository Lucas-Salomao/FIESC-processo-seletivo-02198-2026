"""Extração e chunking dos documentos orientativos (PDF).

Estratégia: percorre o PDF página a página (pymupdf), rastreia o título da
seção corrente (padrão "N. Título" / "N.N Título") e acumula parágrafos em
chunks de ~1200 caracteres com overlap, preservando metadados de rastreio
(documento, seção, página) — são eles que viram as citações exibidas na UI.
"""

import re
from dataclasses import dataclass

import pymupdf

CHUNK_SIZE = 1200
CHUNK_OVERLAP = 150

_SECTION_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\.?\s+([A-ZÀ-Ü].{2,80})$")


@dataclass
class Chunk:
    text: str
    doc: str
    title: str
    section: str
    page: int


def _iter_blocks(pdf_bytes: bytes):
    with pymupdf.open(stream=pdf_bytes, filetype="pdf") as pdf:
        for page_num, page in enumerate(pdf, start=1):
            for block in page.get_text("blocks"):
                text = block[4].strip()
                if text:
                    yield page_num, text


def _iter_blocks_ocr(pdf_bytes: bytes, transcribe):
    """Fallback p/ PDFs sem camada de texto (escaneados): renderiza cada página
    em PNG e transcreve via Gemini multimodal; parágrafos viram blocos."""
    with pymupdf.open(stream=pdf_bytes, filetype="pdf") as pdf:
        for page_num, page in enumerate(pdf, start=1):
            png = page.get_pixmap(dpi=150).tobytes("png")
            text = transcribe(png)
            for block in re.split(r"\n\s*\n", text):
                block = block.strip().lstrip("#").strip()
                if block:
                    yield page_num, block


def chunk_pdf(pdf_bytes: bytes, doc_name: str, title: str, transcribe=None) -> list[Chunk]:
    chunks: list[Chunk] = []
    section = "Introdução"
    buffer = ""
    buffer_page = 1

    def flush(page: int) -> None:
        nonlocal buffer
        text = buffer.strip()
        if len(text) >= 80:  # descarta fragmentos sem conteúdo útil
            chunks.append(Chunk(text=text, doc=doc_name, title=title, section=section, page=page))
        buffer = text[-CHUNK_OVERLAP:] if len(text) > CHUNK_OVERLAP else ""

    def consume(blocks) -> None:
        nonlocal buffer, buffer_page, section
        for page_num, block in blocks:
            match = _SECTION_RE.match(block.splitlines()[0])
            if match:
                flush(buffer_page)
                buffer = ""
                section = f"{match.group(1)} {match.group(2).strip()}"
            if not buffer:
                buffer_page = page_num
            buffer = f"{buffer}\n{block}" if buffer else block
            if len(buffer) >= CHUNK_SIZE:
                flush(page_num)
        flush(buffer_page)

    consume(_iter_blocks(pdf_bytes))
    if not chunks and transcribe is not None:
        # PDF sem camada de texto (escaneado) → OCR via Gemini multimodal
        buffer, buffer_page, section = "", 1, "Introdução"
        consume(_iter_blocks_ocr(pdf_bytes, transcribe))
    return chunks
