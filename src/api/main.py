"""API de Manutenção Prescritiva — FastAPI."""

from datetime import UTC, datetime

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.api import analytics
from src.api.deps import get_chat_agent, get_db, get_llm
from src.core.config import get_settings
from src.core.db import Diagnosis, DocCoverage, Document, Event, FaultFamily
from src.core.schemas import ChatRequest, ChatResponse, DiagnoseResponse, SensorEvent
from src.etl.canonize import get_canonizer
from src.llm.client import LLMClient
from src.ml.diagnose import get_engine_singleton
from src.rag import service as rag_service
from src.rag import store as rag_store

app = FastAPI(
    title="Manutenção Prescritiva — SENAI SC",
    description="Diagnóstico por similaridade histórica + prescrição via RAG documental",
    version="0.1.0",
)

API = "/api/v1"


@app.get(f"{API}/health")
def health() -> dict:
    settings = get_settings()
    artifacts_ok = (settings.artifacts_dir / "lgbm.joblib").exists()
    try:
        families = sorted(rag_store.documented_families())
    except Exception:
        families = []
    return {
        "status": "ok",
        "artifacts_loaded": artifacts_ok,
        "documented_families": families,
        "chat_model": settings.gemini_chat_model,
        "embedding_model": settings.gemini_embedding_model,
    }


@app.post(f"{API}/diagnose", response_model=DiagnoseResponse)
def diagnose(
    event: SensorEvent,
    db: Session = Depends(get_db),
    llm: LLMClient = Depends(get_llm),
) -> DiagnoseResponse:
    engine = get_engine_singleton()
    result = engine.diagnose(event)

    prescription, suggestion, documented = None, None, True
    if result.is_fault:
        documented = rag_store.is_documented(result.family)
        prescription, suggestion = rag_service.prescribe(result.family, event, llm)

    response = DiagnoseResponse(
        predicted_fault=result.family,
        is_fault=result.is_fault,
        probability=round(result.probability, 4),
        knn_agreement=round(result.knn_agreement, 4),
        confidence=result.confidence,
        similar_events=result.similar,
        documented=documented,
        prescription=prescription,
        suggestion=suggestion,
    )

    _persist_diagnosis(db, event, response)
    return response


def _persist_diagnosis(db: Session, event: SensorEvent, response: DiagnoseResponse) -> None:
    """Auditoria do diagnóstico — best-effort (não derruba a resposta se o banco falhar)."""
    try:
        family_id = db.execute(
            select(FaultFamily.id).where(FaultFamily.name == response.predicted_fault)
        ).scalar()
        db.add(
            Diagnosis(
                event_id=event.id,
                predicted_family_id=family_id,
                probability=response.probability,
                neighbors={"ids": response.similar_events.neighbor_ids},
                llm_response=(
                    response.prescription.model_dump() if response.prescription else None
                ),
                created_at=datetime.now(UTC),
            )
        )
        db.commit()
    except Exception:
        db.rollback()


@app.post(f"{API}/chat", response_model=ChatResponse)
async def chat(
    request: ChatRequest,
    llm: LLMClient = Depends(get_llm),
    agent=Depends(get_chat_agent),
) -> ChatResponse:
    try:
        if agent is not None:
            return await agent.ask(request.message, session_id=request.session_id)
        # Fallback: RAG de um passo (sem ADK/credenciais)
        return rag_service.chat(request.message, request.fault_family, request.history, llm)
    except Exception as exc:  # auth expirada/ausente, indisponibilidade do Vertex etc.
        raise HTTPException(
            status_code=503,
            detail=(
                "Serviço de linguagem indisponível. Verifique as credenciais do Vertex AI: "
                "configure GOOGLE_CLOUD_PROJECT no .env e execute "
                "'gcloud auth application-default login'. "
                f"Erro original: {type(exc).__name__}: {exc}"
            ),
        ) from exc


@app.get(f"{API}/stats")
def stats(db: Session = Depends(get_db)) -> dict:
    per_family = db.execute(
        select(FaultFamily.name, FaultFamily.is_fault, func.count(Event.id))
        .join(Event, Event.family_id == FaultFamily.id)
        .group_by(FaultFamily.name, FaultFamily.is_fault)
        .order_by(func.count(Event.id).desc())
    ).all()
    monthly = db.execute(
        select(
            func.date_trunc("month", Event.created_at).label("month"),
            FaultFamily.name,
            func.count(Event.id),
        )
        .join(FaultFamily, Event.family_id == FaultFamily.id)
        .where(FaultFamily.is_fault.is_(True))
        .group_by("month", FaultFamily.name)
        .order_by("month")
    ).all()
    try:
        documented = sorted(rag_store.documented_families())
    except Exception:
        documented = []
    return {
        "per_family": [
            {"family": name, "is_fault": is_fault, "count": count}
            for name, is_fault, count in per_family
        ],
        "monthly_faults": [
            {"month": month.strftime("%Y-%m"), "family": name, "count": count}
            for month, name, count in monthly
        ],
        "documented_families": documented,
    }


@app.get(f"{API}/analytics/severity")
def analytics_severity(db: Session = Depends(get_db)) -> dict:
    """Velocidade RMS por família × regime de RPM (+ severidade relativa ao normal)."""
    return analytics.severity_by_rpm(db.get_bind())


@app.get(f"{API}/analytics/signatures")
def analytics_signatures(db: Session = Depends(get_db)) -> dict:
    """Assinatura (z-scores) de cada família e poder discriminativo por feature."""
    return analytics.fault_signatures(db.get_bind())


@app.get(f"{API}/analytics/model-quality")
def analytics_model_quality(db: Session = Depends(get_db)) -> dict:
    """Métricas do modelo, importância das features e evidência de vazamento por sessão."""
    quality = analytics.model_quality()
    quality["leakage"] = analytics.leakage_evidence(db.get_bind())
    return quality


@app.get(f"{API}/events")
def events(limit: int = 50, family: str | None = None, db: Session = Depends(get_db)) -> list[dict]:
    query = select(Event).order_by(Event.created_at.desc()).limit(min(limit, 500))
    if family:
        query = query.join(FaultFamily).where(FaultFamily.name == family)
    rows = db.execute(query).scalars().all()
    return [
        {
            "id": e.id,
            "created_at": e.created_at.isoformat() if e.created_at else None,
            "raw_fault": e.raw_fault,
            "rpm": e.rpm,
            "x_rms_velocity_mm_s": e.x_rms_velocity_mm_s,
            "z_rms_velocity_mm_s": e.z_rms_velocity_mm_s,
            "temperature_c": e.temperature_c,
        }
        for e in rows
    ]


@app.get(f"{API}/documents")
def list_documents(db: Session = Depends(get_db)) -> dict:
    docs = db.execute(select(Document).where(Document.status == "active")).scalars().all()
    return {
        "documents": [
            {
                "id": d.id,
                "filename": d.filename,
                "title": d.title,
                "ingested_at": d.ingested_at.isoformat(),
            }
            for d in docs
        ],
        "documented_families": sorted(rag_store.documented_families()),
    }


@app.post(f"{API}/documents")
async def upload_document(
    file: UploadFile = File(...),
    title: str = Form(...),
    families: str = Form(..., description="Famílias cobertas, separadas por vírgula"),
    db: Session = Depends(get_db),
    llm: LLMClient = Depends(get_llm),
) -> dict:
    canonizer = get_canonizer()
    family_list = [f.strip() for f in families.split(",") if f.strip()]
    invalid = [f for f in family_list if f not in canonizer.families]
    if invalid:
        raise HTTPException(
            status_code=422,
            detail=f"Famílias inválidas: {invalid}. Válidas: {sorted(canonizer.families)}",
        )
    if not family_list:
        raise HTTPException(status_code=422, detail="Informe ao menos uma família coberta.")

    pdf_bytes = await file.read()
    n_chunks = rag_store.add_document(
        pdf_bytes=pdf_bytes,
        filename=file.filename or "documento.pdf",
        title=title,
        families=family_list,
        llm=llm,
    )
    if n_chunks == 0:
        raise HTTPException(status_code=422, detail="Nenhum conteúdo extraído do PDF.")

    try:
        doc = Document(
            filename=file.filename or "documento.pdf",
            title=title,
            ingested_at=datetime.now(UTC),
            status="active",
        )
        db.add(doc)
        db.flush()
        family_ids = dict(
            db.execute(
                select(FaultFamily.name, FaultFamily.id).where(FaultFamily.name.in_(family_list))
            ).all()
        )
        for fam in family_list:
            if fam in family_ids:
                db.merge(DocCoverage(family_id=family_ids[fam], document_id=doc.id))
        db.commit()
    except Exception:
        db.rollback()

    return {"filename": file.filename, "chunks": n_chunks, "families": family_list}
