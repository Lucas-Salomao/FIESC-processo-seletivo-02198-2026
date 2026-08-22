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
    pass


class FaultFamily(Base):
    __tablename__ = "fault_families"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    is_fault: Mapped[bool] = mapped_column(Boolean)


class LabelMap(Base):
    __tablename__ = "label_map"

    raw_label: Mapped[str] = mapped_column(String(128), primary_key=True)
    family_id: Mapped[int] = mapped_column(ForeignKey("fault_families.id"))


class Event(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    raw_fault: Mapped[str | None] = mapped_column(String(128), nullable=True)
    family_id: Mapped[int | None] = mapped_column(
        ForeignKey("fault_families.id"), nullable=True, index=True
    )

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
    rpm: Mapped[float | None] = mapped_column(Float, nullable=True)


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    filename: Mapped[str] = mapped_column(String(256))
    title: Mapped[str] = mapped_column(String(256))
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(32), default="active")


class DocCoverage(Base):
    __tablename__ = "doc_coverage"

    family_id: Mapped[int] = mapped_column(ForeignKey("fault_families.id"), primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id"), primary_key=True)


class Diagnosis(Base):
    __tablename__ = "diagnoses"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    event_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    predicted_family_id: Mapped[int | None] = mapped_column(
        ForeignKey("fault_families.id"), nullable=True
    )
    probability: Mapped[float] = mapped_column(Float)
    neighbors: Mapped[dict] = mapped_column(JSON, default=dict)
    llm_response: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


def get_engine(url: str | None = None):
    return create_engine(url or get_settings().database_url, pool_pre_ping=True)


def get_session_factory(url: str | None = None):
    return sessionmaker(bind=get_engine(url), expire_on_commit=False)


def create_all(url: str | None = None) -> None:
    Base.metadata.create_all(get_engine(url))
