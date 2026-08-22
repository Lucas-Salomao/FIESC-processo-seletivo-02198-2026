"""Schemas pydantic compartilhados entre API, worker MQTT e ETL.

O schema de evento é ÚNICO: a mesma validação vale para o payload MQTT,
para o POST /diagnose e para as linhas do banner.csv.
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

# Colunas de sensor usadas pelo modelo (unidades SI; as duplicatas em in/s e °F
# do CSV são redundância de unidade e ficam fora do vetor de features).
FEATURE_COLUMNS: list[str] = [
    "z_rms_velocity_mm_s",
    "x_rms_velocity_mm_s",
    "temperature_c",
    "z_peak_acceleration_g",
    "x_peak_acceleration_g",
    "z_peak_vel_comp_freq_hz",
    "x_peak_vel_comp_freq_hz",
    "z_rms_acceleration_g",
    "x_rms_acceleration_g",
    "z_kurtosis",
    "x_kurtosis",
    "z_crest_factor",
    "x_crest_factor",
    "z_peak_velocity_mm_s",
    "x_peak_velocity_mm_s",
    "z_high_freq_rms_accel_g",
    "x_high_freq_rms_accel_g",
    "rpm",
]


class SensorEvent(BaseModel):
    """Evento bruto de telemetria (formato do JSON do enunciado)."""

    model_config = ConfigDict(extra="allow")  # tolera colunas redundantes (in_s, °F)

    id: int | None = None
    created_at: datetime | None = None
    fault: str | None = None  # anotação do operador, quando existir

    z_rms_velocity_mm_s: float
    x_rms_velocity_mm_s: float
    temperature_c: float
    z_peak_acceleration_g: float
    x_peak_acceleration_g: float
    z_peak_vel_comp_freq_hz: float
    x_peak_vel_comp_freq_hz: float
    z_rms_acceleration_g: float
    x_rms_acceleration_g: float
    z_kurtosis: float
    x_kurtosis: float
    z_crest_factor: float
    x_crest_factor: float
    z_peak_velocity_mm_s: float
    x_peak_velocity_mm_s: float
    z_high_freq_rms_accel_g: float
    x_high_freq_rms_accel_g: float
    rpm: float

    def feature_vector(self) -> list[float]:
        return [float(getattr(self, c)) for c in FEATURE_COLUMNS]


class SimilarEvents(BaseModel):
    count: int
    first_seen: datetime | None = None
    last_seen: datetime | None = None
    freq_per_day: float | None = None
    timeline: list[dict] = Field(default_factory=list)  # [{month, count}]
    neighbor_ids: list[int] = Field(default_factory=list)
    neighbor_agreement: float = 0.0  # fração dos k vizinhos na família prevista


class Citation(BaseModel):
    doc: str
    section: str | None = None
    page: int | None = None


class Prescription(BaseModel):
    instructions_md: str
    citations: list[Citation] = Field(default_factory=list)


class DiagnoseResponse(BaseModel):
    predicted_fault: str
    is_fault: bool
    probability: float
    knn_agreement: float
    confidence: str  # "alta" | "media" | "baixa"
    similar_events: SimilarEvents
    documented: bool
    prescription: Prescription | None = None
    suggestion: str | None = None


class ChatRequest(BaseModel):
    message: str
    fault_family: str | None = None  # contexto do diagnóstico corrente (fallback RAG)
    history: list[dict] = Field(default_factory=list)  # [{role, content}] (fallback RAG)
    session_id: str | None = None  # memória de conversa do agente ADK


class ChatResponse(BaseModel):
    answer_md: str
    citations: list[Citation] = Field(default_factory=list)
    documented: bool = True
    agent_used: bool = False  # True = respondido pelo agente ADK; False = RAG de um passo
