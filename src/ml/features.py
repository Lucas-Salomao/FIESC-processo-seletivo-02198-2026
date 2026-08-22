"""Engenharia de features física, compartilhada entre treino e inferência.

Os manuais de manutenção definem assinaturas de falha em ORDENS de rotação
(pico em 1x RPM = desbalanceamento; 1x+2x = desalinhamento/cocked rotor;
BPFO/BPFI/BSF = múltiplos não inteiros), não em Hz absoluto. Converter a
frequência do pico de velocidade para ordem (freq / freq_rotação) torna a
feature invariante à velocidade de operação da sessão de coleta.
"""

import numpy as np
import pandas as pd

from src.core.schemas import FEATURE_COLUMNS

DERIVED_COLUMNS = [
    "x_peak_order",  # ordem do pico (X): freq_pico / freq_rotação
    "z_peak_order",  # ordem do pico (Z)
    "radial_axial_ratio",  # razão RMS X/Z — desbalanceamento é radial, desalinhamento tem axial
    "hf_lf_ratio_x",  # energia de alta freq. vs RMS — defeitos de rolamento excitam HF
    "hf_lf_ratio_z",
]

MODEL_COLUMNS = FEATURE_COLUMNS + DERIVED_COLUMNS

_MAX_ORDER = 50.0
_EPS = 1e-6


def add_derived_features(df: pd.DataFrame) -> pd.DataFrame:
    """Adiciona as features derivadas (recebe DataFrame com FEATURE_COLUMNS)."""
    out = df.copy()
    rot_hz = np.maximum(out["rpm"].to_numpy(dtype=float) / 60.0, _EPS)

    out["x_peak_order"] = np.clip(out["x_peak_vel_comp_freq_hz"] / rot_hz, 0, _MAX_ORDER)
    out["z_peak_order"] = np.clip(out["z_peak_vel_comp_freq_hz"] / rot_hz, 0, _MAX_ORDER)
    out["radial_axial_ratio"] = out["x_rms_velocity_mm_s"] / (out["z_rms_velocity_mm_s"] + _EPS)
    out["hf_lf_ratio_x"] = out["x_high_freq_rms_accel_g"] / (out["x_rms_acceleration_g"] + _EPS)
    out["hf_lf_ratio_z"] = out["z_high_freq_rms_accel_g"] / (out["z_rms_acceleration_g"] + _EPS)
    return out


def feature_matrix(df: pd.DataFrame) -> np.ndarray:
    return add_derived_features(df)[MODEL_COLUMNS].to_numpy(dtype=float)
