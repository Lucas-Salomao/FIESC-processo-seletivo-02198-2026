"""Orquestração RAG: guardrail de cobertura → retrieval → geração com citações.

As citações NÃO são geradas pelo LLM: vêm dos metadados dos chunks realmente
recuperados (determinístico), eliminando citação alucinada.
"""

from src.core.schemas import ChatResponse, Citation, Prescription, SensorEvent
from src.etl.canonize import get_canonizer
from src.llm.client import LLMClient
from src.llm.prompts import SYSTEM_PRESCRIPTIVE, build_chat_prompt, build_prescription_prompt
from src.rag import store

UNDOCUMENTED_MSG = (
    "Ainda não existe documento orientativo cadastrado para a falha **{family}**. "
    "Sugestão: registre um novo documento técnico para este defeito na aba "
    "'Documentos' (procedimento de diagnóstico, correção e validação). "
    "Assim que o documento for cadastrado, o sistema passará a gerar as "
    "instruções de correção automaticamente."
)


def _citations(chunks: list[dict]) -> list[Citation]:
    citations = []
    seen = set()
    for ch in chunks:
        meta = ch["metadata"]
        key = (meta.get("doc"), meta.get("section"), meta.get("page"))
        if key not in seen:
            seen.add(key)
            citations.append(
                Citation(doc=meta.get("doc"), section=meta.get("section"), page=meta.get("page"))
            )
    return citations


def _event_summary(event: SensorEvent) -> str:
    return (
        f"- RPM: {event.rpm:.0f}\n"
        f"- Velocidade RMS (mm/s): X={event.x_rms_velocity_mm_s:.2f}, Z={event.z_rms_velocity_mm_s:.2f}\n"
        f"- Aceleração de pico (g): X={event.x_peak_acceleration_g:.2f}, Z={event.z_peak_acceleration_g:.2f}\n"
        f"- Curtose: X={event.x_kurtosis:.2f}, Z={event.z_kurtosis:.2f}\n"
        f"- Fator de crista: X={event.x_crest_factor:.2f}, Z={event.z_crest_factor:.2f}\n"
        f"- Freq. do pico de velocidade (Hz): X={event.x_peak_vel_comp_freq_hz:.1f}, "
        f"Z={event.z_peak_vel_comp_freq_hz:.1f}\n"
        f"- Temperatura: {event.temperature_c:.1f} °C"
    )


def prescribe(
    family: str, event: SensorEvent, llm: LLMClient
) -> tuple[Prescription | None, str | None]:
    """Retorna (prescrição, None) se a família for documentada; (None, sugestão) caso contrário."""
    display = get_canonizer().families[family].display

    if not store.is_documented(family):
        return None, UNDOCUMENTED_MSG.format(family=display)

    query = f"{display}: diagnóstico, correção, validação e prevenção da falha"
    chunks = store.retrieve(query, family=family, llm=llm)
    prompt = build_prescription_prompt(display, _event_summary(event), chunks)
    answer = llm.generate(prompt, system=SYSTEM_PRESCRIPTIVE)
    return Prescription(instructions_md=answer, citations=_citations(chunks)), None


def chat(message: str, family: str | None, history: list[dict], llm: LLMClient) -> ChatResponse:
    canonizer = get_canonizer()

    if family and not store.is_documented(family):
        display = canonizer.families[family].display
        return ChatResponse(
            answer_md=UNDOCUMENTED_MSG.format(family=display),
            documented=False,
        )

    if family:
        chunks = store.retrieve(message, family=family, llm=llm)
    else:
        # sem contexto de diagnóstico: busca nas famílias documentadas mais prováveis
        chunks = []
        for fam in sorted(store.documented_families()):
            chunks.extend(store.retrieve(message, family=fam, llm=llm, top_k=2))
        chunks = sorted(chunks, key=lambda c: c["distance"])[:6]

    prompt = build_chat_prompt(message, chunks, history)
    answer = llm.generate(prompt, system=SYSTEM_PRESCRIPTIVE)
    return ChatResponse(answer_md=answer, citations=_citations(chunks), documented=True)
