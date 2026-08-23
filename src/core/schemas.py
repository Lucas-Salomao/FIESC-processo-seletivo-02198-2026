"""Schemas pydantic compartilhados entre API, worker MQTT e ETL.

O schema de evento é ÚNICO: a mesma validação vale para o payload MQTT,
para o POST /diagnose e para as linhas do banner.csv.
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

# Colunas de sensor usadas pelo modelo (unidades SI; as duplicatas em in/s e °F
# do CSV são redundância de unidade e ficam fora do vetor de features).
FEATURE_COLUMNS: list[str] = [
    "z_rms_velocity_mm_s",  # velocidade RMS no eixo Z (mm/s)
    "x_rms_velocity_mm_s",  # velocidade RMS no eixo X (mm/s)
    "temperature_c",  # temperatura da máquina (°C)
    "z_peak_acceleration_g",  # aceleração de pico no eixo Z (g)
    "x_peak_acceleration_g",  # aceleração de pico no eixo X (g)
    "z_peak_vel_comp_freq_hz",  # frequência do componente de pico de velocidade, eixo Z (Hz)
    "x_peak_vel_comp_freq_hz",  # idem, eixo X
    "z_rms_acceleration_g",  # aceleração RMS no eixo Z (g)
    "x_rms_acceleration_g",  # aceleração RMS no eixo X (g)
    "z_kurtosis",  # curtose do sinal de vibração no eixo Z (sensível a impactos)
    "x_kurtosis",  # idem, eixo X
    "z_crest_factor",  # fator de crista (pico/RMS) no eixo Z
    "x_crest_factor",  # idem, eixo X
    "z_peak_velocity_mm_s",  # velocidade de pico no eixo Z (mm/s)
    "x_peak_velocity_mm_s",  # idem, eixo X
    "z_high_freq_rms_accel_g",  # aceleração RMS em alta frequência, eixo Z (indício de rolamento)
    "x_high_freq_rms_accel_g",  # idem, eixo X
    "rpm",  # rotação da máquina (rotações por minuto)
]


class SensorEvent(BaseModel):
    """Evento bruto de telemetria (formato do JSON do enunciado).

    Representa uma leitura única dos sensores de vibração de uma máquina.
    É o mesmo formato usado pelo payload MQTT, pelo corpo do POST /diagnose
    e por cada linha do banner.csv.
    """

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
        """Converte o evento em uma lista de números na mesma ordem de
        FEATURE_COLUMNS — é esse vetor que alimenta o modelo de machine learning."""
        return [float(getattr(self, c)) for c in FEATURE_COLUMNS]


class SimilarEvents(BaseModel):
    """Estatísticas sobre ocorrências históricas parecidas com o evento diagnosticado
    (usadas para dar contexto ao usuário: "isso já aconteceu antes? com que frequência?")."""

    count: int
    first_seen: datetime | None = None
    last_seen: datetime | None = None
    freq_per_day: float | None = None
    timeline: list[dict] = Field(default_factory=list)  # [{month, count}]
    neighbor_ids: list[int] = Field(default_factory=list)
    neighbor_agreement: float = 0.0  # fração dos k vizinhos na família prevista


class Citation(BaseModel):
    """Referência a um trecho de documento que foi usado para montar uma resposta (RAG)."""

    doc: str
    section: str | None = None
    page: int | None = None


class Prescription(BaseModel):
    """Instruções de correção geradas pelo LLM, junto com as fontes documentais citadas."""

    instructions_md: str
    citations: list[Citation] = Field(default_factory=list)


class DiagnoseResponse(BaseModel):
    """Resposta completa devolvida pelo endpoint POST /diagnose."""

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
    """Mensagem enviada pelo usuário ao endpoint POST /chat."""

    message: str
    fault_family: str | None = None  # contexto do diagnóstico corrente (fallback RAG)
    history: list[dict] = Field(default_factory=list)  # [{role, content}] (fallback RAG)
    session_id: str | None = None  # memória de conversa do agente ADK


class ChatResponse(BaseModel):
    """Resposta devolvida pelo endpoint POST /chat."""

    answer_md: str
    citations: list[Citation] = Field(default_factory=list)
    documented: bool = True
    agent_used: bool = False  # True = respondido pelo agente ADK; False = RAG de um passo
