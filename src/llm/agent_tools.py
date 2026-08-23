"""Ferramentas do agente de manutenção (Google ADK).

Funções puras, JSON-serializáveis e testáveis offline. As docstrings em
PT-BR viram a descrição da ferramenta apresentada ao LLM.

Anti-alucinação por construção:
- O guardrail de cobertura documental fica DENTRO de `buscar_documentos` —
  família sem documento retorna um aviso estruturado; o LLM nunca decide.
- As citações exibidas na UI vêm do coletor (ContextVar) alimentado pelo
  retrieval — nunca do texto gerado pelo modelo.
"""

from contextvars import ContextVar
from datetime import timedelta

from pydantic import ValidationError
from sqlalchemy import func, select

from src.core.db import Document, Event, FaultFamily, get_session_factory
from src.core.schemas import SensorEvent
from src.etl.canonize import get_canonizer
from src.llm.client import get_llm_client
from src.rag import store
from src.rag.service import UNDOCUMENTED_MSG

_citations: ContextVar[list[dict] | None] = ContextVar("agent_citations", default=None)


def reset_citations() -> None:
    """Limpa o coletor de citações — chamado no início de cada pergunta do chat."""
    _citations.set([])


def get_citations() -> list[dict]:
    """Devolve as citações acumuladas durante o processamento da pergunta atual."""
    return _citations.get() or []


def _record_citations(chunks: list[dict]) -> None:
    """Guarda no coletor (ContextVar) a origem de cada trecho retornado pelo RAG,
    sem duplicar entradas já vistas. É esta lista, e não o texto gerado pelo
    LLM, que alimenta as citações exibidas na UI."""
    collected = _citations.get()
    if collected is None:
        return
    seen = {(c["doc"], c.get("section"), c.get("page")) for c in collected}
    for ch in chunks:
        meta = ch["metadata"]
        key = (meta.get("doc"), meta.get("section"), meta.get("page"))
        if key not in seen:
            seen.add(key)
            collected.append(
                {"doc": meta.get("doc"), "section": meta.get("section"), "page": meta.get("page")}
            )


def _familia_invalida(familia: str) -> dict:
    """Resposta padrão de erro quando o LLM informa uma família que não existe."""
    validas = sorted(get_canonizer().families)
    return {
        "erro": f"Família '{familia}' não existe.",
        "familias_validas": validas,
    }


def consultar_historico(familia: str | None = None, dias: int = 30) -> dict:
    """Consulta o histórico de eventos no banco corporativo (PostgreSQL).

    Use para responder perguntas sobre quantidade de ocorrências, frequência,
    período e tendência de falhas. `familia` filtra por uma família canônica
    (ex.: 'cocked_rotor', 'desalinhamento'); omita para o resumo geral.
    `dias` limita a janela de tempo a partir do evento mais recente do banco.
    """
    canonizer = get_canonizer()
    if familia and familia not in canonizer.families:
        return _familia_invalida(familia)

    with get_session_factory()() as session:
        latest = session.execute(select(func.max(Event.created_at))).scalar()
        if latest is None:
            return {"erro": "Banco de eventos vazio — execute a ingestão do banner.csv."}
        window_start = latest - timedelta(days=dias)

        query = (
            select(FaultFamily.name, FaultFamily.is_fault, func.count(Event.id))
            .join(Event, Event.family_id == FaultFamily.id)
            .where(Event.created_at >= window_start)
            .group_by(FaultFamily.name, FaultFamily.is_fault)
            .order_by(func.count(Event.id).desc())
        )
        if familia:
            query = query.where(FaultFamily.name == familia)
        rows = session.execute(query).all()

        detalhe = None
        if familia:
            first, last, total = session.execute(
                select(func.min(Event.created_at), func.max(Event.created_at), func.count(Event.id))
                .join(FaultFamily, Event.family_id == FaultFamily.id)
                .where(FaultFamily.name == familia)
            ).one()
            if total:
                span_days = max((last - first).total_seconds() / 86400.0, 1.0)
                detalhe = {
                    "total_historico": int(total),
                    "primeira_ocorrencia": first.isoformat(),
                    "ultima_ocorrencia": last.isoformat(),
                    "frequencia_por_dia": round(total / span_days, 2),
                }

    return {
        "janela_dias": dias,
        "janela_fim": latest.isoformat(),
        "ocorrencias_na_janela": [
            {"familia": name, "eh_falha": is_fault, "quantidade": int(count)}
            for name, is_fault, count in rows
        ],
        "detalhe_familia": detalhe,
    }


def buscar_documentos(consulta: str, familia: str) -> dict:
    """Busca trechos nos documentos técnicos orientativos (base RAG).

    OBRIGATÓRIO antes de descrever qualquer procedimento de correção,
    diagnóstico ou prevenção. `familia` é a família canônica da falha
    (ex.: 'rolamento_inner'). Se a família não tiver documento cadastrado,
    retorna documentado=False com o aviso a ser transmitido ao usuário.
    """
    canonizer = get_canonizer()
    if familia not in canonizer.families:
        return _familia_invalida(familia)

    if not store.is_documented(familia):
        display = canonizer.families[familia].display
        return {"documentado": False, "aviso": UNDOCUMENTED_MSG.format(family=display)}

    chunks = store.retrieve(consulta, family=familia, llm=get_llm_client())
    _record_citations(chunks)
    return {
        "documentado": True,
        "trechos": [
            {
                "n": i + 1,
                "texto": ch["text"],
                "fonte": {
                    "doc": ch["metadata"].get("doc"),
                    "secao": ch["metadata"].get("section"),
                    "pagina": ch["metadata"].get("page"),
                },
            }
            for i, ch in enumerate(chunks)
        ],
    }


def diagnosticar_evento(evento_json: str) -> dict:
    """Diagnostica um evento de sensores de vibração a partir do seu JSON bruto.

    Use quando o usuário colar um JSON de evento no chat. Executa o motor de
    diagnóstico (LightGBM + KNN) e retorna a família de falha prevista,
    probabilidade, concordância dos vizinhos e estatísticas de ocorrências
    similares no histórico.
    """
    from src.ml.diagnose import get_engine_singleton

    try:
        event = SensorEvent.model_validate_json(evento_json)
    except ValidationError as exc:
        return {
            "erro": "JSON de evento inválido.",
            "detalhes": [f"{e['loc']}: {e['msg']}" for e in exc.errors()[:8]],
        }

    result = get_engine_singleton().diagnose(event)
    canonizer = get_canonizer()
    return {
        "familia_prevista": result.family,
        "descricao": canonizer.families[result.family].display,
        "eh_falha": result.is_fault,
        "probabilidade": round(result.probability, 4),
        "concordancia_knn": round(result.knn_agreement, 4),
        "confianca": result.confidence,
        "documentada": store.is_documented(result.family) if result.is_fault else None,
        "ocorrencias_similares": {
            "total": result.similar.count,
            "frequencia_por_dia": result.similar.freq_per_day,
            "primeira": result.similar.first_seen.isoformat()
            if result.similar.first_seen
            else None,
            "ultima": result.similar.last_seen.isoformat() if result.similar.last_seen else None,
        },
    }


def cobertura_documental() -> dict:
    """Lista quais famílias de falha possuem documento orientativo cadastrado.

    Use para responder 'quais falhas têm procedimento documentado?' ou antes
    de sugerir o registro de um novo documento.
    """
    canonizer = get_canonizer()
    documented = store.documented_families()
    fault_families = [f for f in canonizer.families.values() if f.is_fault]

    with get_session_factory()() as session:
        docs = session.execute(select(Document).where(Document.status == "active")).scalars().all()

    return {
        "documentadas": sorted(f.name for f in fault_families if f.name in documented),
        "sem_documento": sorted(f.name for f in fault_families if f.name not in documented),
        "documentos_cadastrados": [
            {"arquivo": d.filename, "titulo": d.title, "ingerido_em": d.ingested_at.isoformat()}
            for d in docs
        ],
    }


# Lista de ferramentas registradas no LlmAgent (src/llm/agent.py). O nome da
# função e a docstring de cada uma são o que o LLM vê para decidir quando usá-la.
AGENT_TOOLS = [consultar_historico, buscar_documentos, diagnosticar_evento, cobertura_documental]
