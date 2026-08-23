"""Severidade vibracional — ISO 10816 (velocidade RMS em mm/s).

Domínio reutilizável: hoje alimenta o dashboard; pode enriquecer o /diagnose.

As zonas absolutas dependem da CLASSE da máquina (potência e rigidez da
fundação), por isso a classe é parâmetro. Como este dataset vem de bancada
com falhas induzidas, a leitura mais defensável no dia a dia é a severidade
RELATIVA à linha de base da própria máquina no mesmo regime de rotação —
por isso `relative_severity` existe ao lado da classificação normativa.
"""

from dataclasses import dataclass

# Limites A/B, B/C e C/D de velocidade RMS (mm/s) por classe ISO 10816.
ISO_10816_ZONES: dict[str, tuple[float, float, float]] = {
    "I": (0.71, 1.80, 4.50),  # máquinas pequenas (até ~15 kW)
    "II": (1.12, 2.80, 7.10),  # máquinas médias (15–75 kW)
    "III": (1.80, 4.50, 11.2),  # máquinas grandes, fundação rígida
    "IV": (2.80, 7.10, 18.0),  # máquinas grandes, fundação flexível
}

ZONE_MEANING: dict[str, str] = {
    "A": "Máquina recém-comissionada",
    "B": "Operação contínua aceitável",
    "C": "Insatisfatória — planejar intervenção",
    "D": "Inaceitável — risco de dano",
}

DEFAULT_CLASS = "I"


@dataclass(frozen=True)
class ZoneBand:
    zone: str
    lower: float
    upper: float | None  # None = sem limite superior


def zone_bands(machine_class: str = DEFAULT_CLASS) -> list[ZoneBand]:
    """Faixas contíguas das zonas, para desenhar as bandas no gráfico."""
    ab, bc, cd = _limits(machine_class)
    return [
        ZoneBand("A", 0.0, ab),
        ZoneBand("B", ab, bc),
        ZoneBand("C", bc, cd),
        ZoneBand("D", cd, None),
    ]


def classify_zone(rms_mm_s: float, machine_class: str = DEFAULT_CLASS) -> str:
    """Classifica uma leitura de vibração (RMS em mm/s) em uma zona ISO 10816 (A a D).

    Quanto maior a letra, piores as condições: A = recém-comissionada,
    B = aceitável, C = insatisfatória, D = inaceitável (ver ZONE_MEANING).
    """
    ab, bc, cd = _limits(machine_class)
    if rms_mm_s <= ab:
        return "A"
    if rms_mm_s <= bc:
        return "B"
    if rms_mm_s <= cd:
        return "C"
    return "D"


def relative_severity(rms_mm_s: float, baseline_rms_mm_s: float | None) -> float | None:
    """Quantas vezes a vibração excede a linha de base da própria máquina.

    baseline_rms_mm_s deve vir do estado `normal` NO MESMO RPM — comparar
    regimes de rotação diferentes não tem significado físico.
    """
    if not baseline_rms_mm_s or baseline_rms_mm_s <= 0:
        return None
    return round(rms_mm_s / baseline_rms_mm_s, 2)


def _limits(machine_class: str) -> tuple[float, float, float]:
    try:
        return ISO_10816_ZONES[machine_class]
    except KeyError:
        raise ValueError(
            f"Classe ISO inválida: {machine_class!r}. Use uma de {sorted(ISO_10816_ZONES)}."
        ) from None
