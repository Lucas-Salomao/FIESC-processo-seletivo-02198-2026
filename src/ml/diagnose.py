"""Motor de diagnóstico em inferência: LightGBM (tipo de defeito + probabilidade)
combinado com KNN (ocorrências históricas similares).

A confiança final cruza as duas evidências independentes:
- probabilidade calibrada do classificador;
- concordância dos k vizinhos mais próximos com a classe prevista.
"""

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

from src.core.config import get_settings
from src.core.schemas import FEATURE_COLUMNS, SensorEvent, SimilarEvents
from src.etl.canonize import get_canonizer
from src.ml.features import feature_matrix


@dataclass
class DiagnosisResult:
    """Resultado de um diagnóstico: família prevista, se é falha, o quão
    confiante o sistema está e o que mais parecido já ocorreu no histórico."""

    family: str
    is_fault: bool
    probability: float
    knn_agreement: float
    confidence: str
    similar: SimilarEvents


class DiagnosisEngine:
    """Carrega os artefatos treinados (scaler, classificador LightGBM, índice
    KNN e metadados históricos) e usa-os para diagnosticar novos eventos."""

    def __init__(self, artifacts_dir: Path):
        self.scaler = joblib.load(artifacts_dir / "scaler.joblib")
        self.clf = joblib.load(artifacts_dir / "lgbm.joblib")
        self.knn = joblib.load(artifacts_dir / "knn.joblib")
        self.index_meta: pd.DataFrame = joblib.load(artifacts_dir / "index_meta.joblib")
        self.canonizer = get_canonizer()

    def diagnose(self, event: SensorEvent) -> DiagnosisResult:
        """Classifica um evento de sensor e devolve o diagnóstico completo."""
        # 1. Monta o vetor de features do evento e aplica a mesma normalização
        #    (scaler) usada no treino.
        raw = pd.DataFrame([dict(zip(FEATURE_COLUMNS, event.feature_vector(), strict=False))])
        x = self.scaler.transform(feature_matrix(raw))

        # 2. Classificador LightGBM: pega a classe (família) de maior probabilidade.
        proba = self.clf.predict_proba(x)[0]
        idx_best = int(np.argmax(proba))
        family = str(self.clf.classes_[idx_best])
        probability = float(proba[idx_best])

        # 3. KNN: busca os eventos históricos mais próximos e mede quantos
        #    deles pertencem à mesma família prevista pelo classificador —
        #    é uma segunda opinião, independente do LightGBM.
        _, neighbor_idx = self.knn.kneighbors(x)
        neighbors = self.index_meta.iloc[neighbor_idx[0]]
        agreement = float((neighbors["family"] == family).mean())

        similar = self._similar_events(family, neighbors)
        confidence = self._confidence(probability, agreement)
        is_fault = self.canonizer.families[family].is_fault

        return DiagnosisResult(
            family=family,
            is_fault=is_fault,
            probability=probability,
            knn_agreement=agreement,
            confidence=confidence,
            similar=similar,
        )

    def _similar_events(self, family: str, neighbors: pd.DataFrame) -> SimilarEvents:
        """Monta as estatísticas de ocorrências históricas da família prevista:
        quantas vezes já ocorreu, desde quando, com que frequência e a
        distribuição mensal (para o gráfico de linha do tempo na UI)."""
        history = self.index_meta[self.index_meta["family"] == family]
        if history.empty:
            return SimilarEvents(count=0)

        first = history["created_at"].min()
        last = history["created_at"].max()
        span_days = max((last - first).total_seconds() / 86400.0, 1.0)

        # Agrupa as ocorrências por mês para desenhar a linha do tempo.
        monthly = history.set_index("created_at").resample("MS").size().reset_index(name="count")
        timeline = [
            {"month": ts.strftime("%Y-%m"), "count": int(c)}
            for ts, c in zip(monthly["created_at"], monthly["count"], strict=False)
            if c > 0
        ]

        return SimilarEvents(
            count=int(len(history)),
            first_seen=first.to_pydatetime(),
            last_seen=last.to_pydatetime(),
            freq_per_day=round(len(history) / span_days, 2),
            timeline=timeline,
            neighbor_ids=[int(i) for i in neighbors["id"].tolist()],
            neighbor_agreement=float((neighbors["family"] == family).mean()),
        )

    @staticmethod
    def _confidence(probability: float, agreement: float) -> str:
        """Combina a probabilidade do classificador com a concordância do KNN
        em um rótulo de confiança fácil de entender: alta, média ou baixa."""
        score = probability * agreement
        if score >= 0.7:
            return "alta"
        if score >= 0.4:
            return "media"
        return "baixa"


@lru_cache
def get_engine_singleton() -> DiagnosisEngine:
    """Devolve a instância única do motor de diagnóstico (os artefatos são
    carregados do disco uma única vez por processo)."""
    return DiagnosisEngine(get_settings().artifacts_dir)


__all__ = ["DiagnosisEngine", "DiagnosisResult", "get_engine_singleton", "FEATURE_COLUMNS"]
