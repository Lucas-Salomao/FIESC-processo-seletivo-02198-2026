"""Modelos SQLAlchemy do banco corporativo (PostgreSQL)."""

from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    create_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from src.core.config import get_settings


class Base(DeclarativeBase):
    """Classe base de todos os modelos abaixo — é ela que o SQLAlchemy usa para
    saber quais classes representam tabelas do banco."""


class FaultFamily(Base):
    """Família canônica de falha (ex.: rolamento_inner, desalinhamento, normal).

    `is_fault` diferencia famílias que são falhas reais das que são apenas
    estados operacionais (ex.: 'normal', 'baseline', 'teste', 'acelerando').
    """

    __tablename__ = "fault_families"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    is_fault: Mapped[bool] = mapped_column(Boolean)


class LabelMap(Base):
    """Registro de auditoria da canonicalização: para cada rótulo bruto do
    dataset (ex.: 'rolamento_inner_2', 'ddesbalanceado_adxl_0'), guarda a
    família canônica para a qual ele foi mapeado (ver src/etl/canonize.py)."""

    __tablename__ = "label_map"

    raw_label: Mapped[str] = mapped_column(String(128), primary_key=True)
    family_id: Mapped[int] = mapped_column(ForeignKey("fault_families.id"))


class Event(Base):
    """Uma leitura dos sensores de vibração de uma máquina em um instante de tempo.

    Os campos de `z_rms_velocity_mm_s` a `rpm` são as mesmas features físicas
    usadas pelo modelo de diagnóstico — a lista e o significado de cada uma
    estão em FEATURE_COLUMNS, em src/core/schemas.py.
    """

    __tablename__ = "events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    raw_fault: Mapped[str | None] = mapped_column(
        String(128), nullable=True
    )  # rótulo bruto original
    family_id: Mapped[int | None] = mapped_column(
        ForeignKey("fault_families.id"), nullable=True, index=True
    )  # família canônica já resolvida (None enquanto não for classificado)

    z_rms_velocity_mm_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    x_rms_velocity_mm_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    temperature_c: Mapped[float | None] = mapped_column(Float, nullable=True)
    z_peak_acceleration_g: Mapped[float | None] = mapped_column(Float, nullable=True)
    x_peak_acceleration_g: Mapped[float | None] = mapped_column(Float, nullable=True)
    z_peak_vel_comp_freq_hz: Mapped[float | None] = mapped_column(Float, nullable=True)
    x_peak_vel_comp_freq_hz: Mapped[float | None] = mapped_column(Float, nullable=True)
    z_rms_acceleration_g: Mapped[float | None] = mapped_column(Float, nullable=True)
    x_rms_acceleration_g: Mapped[float | None] = mapped_column(Float, nullable=True)
    z_kurtosis: Mapped[float | None] = mapped_column(Float, nullable=True)
    x_kurtosis: Mapped[float | None] = mapped_column(Float, nullable=True)
    z_crest_factor: Mapped[float | None] = mapped_column(Float, nullable=True)
    x_crest_factor: Mapped[float | None] = mapped_column(Float, nullable=True)
    z_peak_velocity_mm_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    x_peak_velocity_mm_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    z_high_freq_rms_accel_g: Mapped[float | None] = mapped_column(Float, nullable=True)
    x_high_freq_rms_accel_g: Mapped[float | None] = mapped_column(Float, nullable=True)
    rpm: Mapped[float | None] = mapped_column(
        Float, nullable=True
    )  # rotação da máquina (rot./min.)


class Document(Base):
    """Um documento PDF orientativo cadastrado no sistema (procedimento de manutenção)."""

    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    filename: Mapped[str] = mapped_column(String(256))
    title: Mapped[str] = mapped_column(String(256))
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(
        String(32), default="active"
    )  # "active" = visível no sistema


class DocCoverage(Base):
    """Tabela de associação: registra quais famílias de falha cada documento cobre
    (um documento pode cobrir várias famílias, e cada família pode ter vários documentos)."""

    __tablename__ = "doc_coverage"

    family_id: Mapped[int] = mapped_column(ForeignKey("fault_families.id"), primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id"), primary_key=True)


class Diagnosis(Base):
    """Registro de auditoria de cada diagnóstico feito pela API: o que foi
    previsto, com qual probabilidade, quais vizinhos históricos foram usados
    e o que o LLM respondeu como prescrição (quando houver)."""

    __tablename__ = "diagnoses"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    event_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    predicted_family_id: Mapped[int | None] = mapped_column(
        ForeignKey("fault_families.id"), nullable=True
    )
    probability: Mapped[float] = mapped_column(Float)
    neighbors: Mapped[dict] = mapped_column(JSON, default=dict)  # ids dos vizinhos KNN consultados
    llm_response: Mapped[dict | None] = mapped_column(
        JSON, nullable=True
    )  # prescrição gerada pelo LLM
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


def get_engine(url: str | None = None):
    """Cria a "engine" de conexão do SQLAlchemy com o banco (usa a URL da
    configuração quando `url` não é informado)."""
    return create_engine(url or get_settings().database_url, pool_pre_ping=True)


def get_session_factory(url: str | None = None):
    """Fábrica de sessões do SQLAlchemy — cada chamada gera uma nova sessão
    (conexão de trabalho) para conversar com o banco."""
    return sessionmaker(bind=get_engine(url), expire_on_commit=False)


def create_all(url: str | None = None) -> None:
    """Cria todas as tabelas declaradas acima no banco, caso ainda não existam."""
    Base.metadata.create_all(get_engine(url))
