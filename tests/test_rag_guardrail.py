"""Guardrail de cobertura documental + pipeline RAG (com LLM fake e PDF gerado)."""

import pymupdf
import pytest

from src.rag import service, store


def _make_pdf(paragraphs: list[str]) -> bytes:
    doc = pymupdf.open()
    page = doc.new_page()
    y = 72
    for text in paragraphs:
        page.insert_text((72, y), text, fontsize=11)
        y += 40
    return doc.tobytes()


@pytest.fixture(scope="module")
def indexed_doc(fake_llm, env_dirs):
    pdf = _make_pdf(
        [
            "1. Objetivo",
            "Este documento descreve o procedimento de correcao do rotor inclinado "
            "(cocked rotor) em maquinas rotativas, com foco em seguranca e validacao. "
            "Antes de qualquer intervencao, desligar o equipamento e aplicar bloqueio.",
            "2. Correcao",
            "Remover o rotor, limpar superficies, verificar assentamento e reinstalar "
            "corretamente aplicando o torque adequado conforme especificacao tecnica.",
        ]
    )
    n_chunks = store.add_document(
        pdf_bytes=pdf,
        filename="DocTeste.pdf",
        title="Procedimento Cocked Rotor",
        families=["cocked_rotor"],
        llm=fake_llm,
    )
    assert n_chunks > 0
    return n_chunks


def test_ocr_fallback_pdf_sem_texto(fake_llm, env_dirs):
    """PDF escaneado (só imagens) → chunking cai no OCR via LLM multimodal."""
    import pymupdf

    from src.rag.chunking import chunk_pdf

    # gera um PDF cuja única página contém apenas uma imagem (sem camada de texto)
    src_doc = pymupdf.open()
    page = src_doc.new_page()
    page.insert_text((72, 72), "conteudo que vira imagem")
    png = page.get_pixmap(dpi=100).tobytes("png")

    img_doc = pymupdf.open()
    img_page = img_doc.new_page()
    img_page.insert_image(img_page.rect, stream=png)
    pdf_bytes = img_doc.tobytes()

    chunks = chunk_pdf(pdf_bytes, doc_name="Scan.pdf", title="Scan", transcribe=fake_llm.transcribe)
    assert chunks
    assert "OCR" in chunks[0].text
    assert chunks[0].section.startswith("1 Objetivo")


def test_is_documented(indexed_doc):
    assert store.is_documented("cocked_rotor") is True
    assert store.is_documented("ventoinha") is False


def test_documented_families(indexed_doc):
    assert "cocked_rotor" in store.documented_families()


def test_retrieve_filters_by_family(indexed_doc, fake_llm):
    chunks = store.retrieve("como corrigir rotor inclinado", family="cocked_rotor", llm=fake_llm)
    assert chunks
    assert all(c["metadata"]["family"] == "cocked_rotor" for c in chunks)


def test_prescribe_documented(indexed_doc, fake_llm, sample_event):
    prescription, suggestion = service.prescribe("cocked_rotor", sample_event, fake_llm)
    assert suggestion is None
    assert prescription is not None
    assert "Instruções" in prescription.instructions_md
    assert prescription.citations
    assert prescription.citations[0].doc == "DocTeste.pdf"


def test_prescribe_undocumented_suggests_registration(indexed_doc, fake_llm, sample_event):
    """Requisito do edital: falha sem documento → avisar e sugerir registro."""
    prescription, suggestion = service.prescribe("ventoinha", sample_event, fake_llm)
    assert prescription is None
    assert suggestion is not None
    assert "não existe documento" in suggestion
    assert "registre um novo documento" in suggestion


def test_undocumented_never_calls_llm(indexed_doc, fake_llm, sample_event):
    """Anti-alucinação: o guardrail bloqueia ANTES de qualquer chamada ao LLM."""
    calls_before = len(fake_llm.generate_calls)
    service.prescribe("eccentric_rotor", sample_event, fake_llm)
    assert len(fake_llm.generate_calls) == calls_before


def test_chat_undocumented_family(indexed_doc, fake_llm):
    response = service.chat("como corrigir?", "ventoinha", [], fake_llm)
    assert response.documented is False
    assert "não existe documento" in response.answer_md


def test_chat_documented_family(indexed_doc, fake_llm):
    response = service.chat("como corrigir o rotor?", "cocked_rotor", [], fake_llm)
    assert response.documented is True
    assert response.citations
