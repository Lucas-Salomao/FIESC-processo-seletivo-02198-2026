"""Paleta e chrome dos gráficos.

As cores NÃO foram escolhidas por gosto: cada conjunto passou pelo validador de
paleta (banda de luminosidade, piso de croma, separação para daltonismo e
contraste contra a superfície) nos modos claro e escuro.

Registro das decisões:
- documentada × sem documento começou como verde/vermelho e **falhou** a
  separação para deuteranopia (ΔE 4,1 — muito abaixo do piso 6). Trocado por
  azul/laranja (ΔE 24,7 claro / 26,8 escuro), com ícone + rótulo na legenda
  como codificação secundária.
- Regime de RPM é grandeza ORDENADA, não identidade: usa rampa ordinal de um
  único tom (azul), monotônica em luminosidade. Rotação maior = mais
  contraste contra a superfície nos dois modos.
- z-score é polaridade (acima/abaixo da média global): escala DIVERGENTE
  azul ↔ vermelho com cinza neutro no meio — nunca arco-íris.
"""

from dataclasses import dataclass

import streamlit as st


@dataclass(frozen=True)
class Palette:
    surface: str
    text_primary: str
    text_secondary: str
    muted: str
    grid: str
    axis: str
    # categóricos (ordem fixa, nunca ciclada)
    series: tuple[str, ...]
    # binário documentada / sem documento
    documented: str
    undocumented: str
    # rampa ordinal p/ regime de RPM (menor → maior rotação)
    rpm_ramp: tuple[str, str, str, str]
    # divergente p/ z-scores
    diverging: tuple[str, ...]
    # destaque de alerta (feature problemática)
    alert: str


LIGHT = Palette(
    surface="#fcfcfb",
    text_primary="#0b0b0b",
    text_secondary="#52514e",
    muted="#898781",
    grid="#e1e0d9",
    axis="#c3c2b7",
    series=("#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300"),
    documented="#2a78d6",
    undocumented="#eb6834",
    rpm_ramp=("#86b6ef", "#5598e7", "#2a78d6", "#184f95"),
    diverging=("#184f95", "#2a78d6", "#9ec5f4", "#f0efec", "#f0b3b3", "#e34948", "#a82f2f"),
    alert="#d03b3b",
)

DARK = Palette(
    surface="#1a1a19",
    text_primary="#ffffff",
    text_secondary="#c3c2b7",
    muted="#898781",
    grid="#2c2c2a",
    axis="#383835",
    series=("#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300"),
    documented="#3987e5",
    undocumented="#d95926",
    rpm_ramp=("#184f95", "#2a78d6", "#5598e7", "#86b6ef"),
    diverging=("#86b6ef", "#5598e7", "#2a78d6", "#383835", "#c96b6b", "#e34948", "#f0a0a0"),
    alert="#e66767",
)

FONT = 'system-ui, -apple-system, "Segoe UI", sans-serif'


def palette() -> Palette:
    """Paleta do tema ativo do Streamlit (cai no escuro se indisponível)."""
    try:
        return LIGHT if st.context.theme.type == "light" else DARK
    except Exception:
        return DARK


def style(fig, height: int = 420, legend: bool = True):
    """Chrome comum: superfície transparente, eixos recessivos, tipografia do sistema."""
    p = palette()
    fig.update_layout(
        height=height,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font={"family": FONT, "color": p.text_secondary, "size": 13},
        title={"font": {"color": p.text_primary, "size": 16}},
        margin={"l": 10, "r": 20, "t": 56, "b": 40},
        showlegend=legend,
        legend={"orientation": "h", "yanchor": "bottom", "y": 1.0, "x": 0, "title": ""},
        hoverlabel={"font": {"family": FONT, "size": 12}},
    )
    fig.update_xaxes(gridcolor=p.grid, linecolor=p.axis, zerolinecolor=p.axis, title_standoff=8)
    fig.update_yaxes(gridcolor=p.grid, linecolor=p.axis, zerolinecolor=p.axis, title_standoff=8)
    return fig


def diverging_scale() -> list[list]:
    """Escala plotly a partir dos polos divergentes (passos equidistantes)."""
    colors = palette().diverging
    step = 1 / (len(colors) - 1)
    return [[i * step, c] for i, c in enumerate(colors)]


def rpm_color_map(rpms: list[float]) -> dict[float, str]:
    """Mapeia cada regime de RPM a um degrau da rampa ordinal (ordem crescente)."""
    ramp = palette().rpm_ramp
    ordered = sorted(rpms)
    if not ordered:
        return {}
    return {
        rpm: ramp[min(int(i * len(ramp) / len(ordered)), len(ramp) - 1)]
        for i, rpm in enumerate(ordered)
    }
