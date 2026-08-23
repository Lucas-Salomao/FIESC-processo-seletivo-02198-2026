"""Dashboard analítico — 4 sub-abas + filtro global.

Cada painel responde a uma pergunta que o time de manutenção realmente faz:
  Visão geral        → onde estão as falhas e o que falta documentar?
  Severidade & Física→ essa vibração é grave, e os dados batem com os manuais?
  Assinaturas        → o que distingue cada falha (e o que não distingue)?
  Qualidade do modelo→ posso confiar no diagnóstico automático?

A análise exploratória que motivou cada painel está em ANALYTICS.md.
"""

import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st

from src.core.severity import DEFAULT_CLASS, ISO_10816_ZONES, ZONE_MEANING, classify_zone
from src.ui.api_client import api_get
from src.ui.theme import diverging_scale, palette, rpm_color_map, style

FEATURE_LABELS = {
    "x_rms_velocity_mm_s": "vel RMS X",
    "z_rms_velocity_mm_s": "vel RMS Z",
    "x_peak_velocity_mm_s": "vel pico X",
    "z_peak_velocity_mm_s": "vel pico Z",
    "x_rms_acceleration_g": "acel RMS X",
    "z_rms_acceleration_g": "acel RMS Z",
    "x_peak_acceleration_g": "acel pico X",
    "z_peak_acceleration_g": "acel pico Z",
    "x_high_freq_rms_accel_g": "acel HF X",
    "z_high_freq_rms_accel_g": "acel HF Z",
    "x_peak_vel_comp_freq_hz": "freq pico X",
    "z_peak_vel_comp_freq_hz": "freq pico Z",
    "x_kurtosis": "curtose X",
    "z_kurtosis": "curtose Z",
    "x_crest_factor": "crista X",
    "z_crest_factor": "crista Z",
    "temperature_c": "temperatura",
    "rpm": "rotação",
    "x_peak_order": "ordem pico X",
    "z_peak_order": "ordem pico Z",
    "radial_axial_ratio": "razão radial/axial",
    "hf_lf_ratio_x": "razão HF/LF X",
    "hf_lf_ratio_z": "razão HF/LF Z",
}


def _label(feature: str) -> str:
    return FEATURE_LABELS.get(feature, feature)


# --------------------------------------------------------------------------
# Entrada — um painel por vez, escolhido na navegação lateral
# --------------------------------------------------------------------------
PANEL_OVERVIEW = "Visão geral"
PANEL_SEVERITY = "Severidade & Física"
PANEL_SIGNATURES = "Assinaturas"
PANEL_QUALITY = "Qualidade do modelo"

_EMPTY_SEVERITY = {"records": [], "baseline_by_rpm": {}}
_EMPTY_SIGNATURES = {"signatures": [], "discriminative_power": []}


def render(panel: str | None) -> None:
    """Renderiza só o painel ativo.

    Cada painel busca apenas os dados de que precisa: como a navegação
    mostra um de cada vez, deixamos de disparar as três consultas
    analíticas a cada rerun.
    """
    if panel == PANEL_SEVERITY:
        _render_severity()
    elif panel == PANEL_SIGNATURES:
        _render_signatures()
    elif panel == PANEL_QUALITY:
        _panel_model_quality(_safe_get("/analytics/model-quality", {}))
    else:
        _render_overview()


def _safe_get(path: str, fallback: dict) -> dict:
    try:
        return api_get(path)
    except requests.RequestException:
        return fallback


def _load_stats() -> dict | None:
    """Estatísticas base; None quando não há dados para exibir."""
    try:
        stats = api_get("/stats")
    except requests.RequestException as exc:
        st.warning(f"Sem dados — execute `python scripts/ingest_data.py` ({exc})")
        return None
    if not stats.get("per_family"):
        st.info("Banco vazio — execute a ingestão do banner.csv.")
        return None
    return stats


def _severity_frame() -> pd.DataFrame:
    return pd.DataFrame(_safe_get("/analytics/severity", _EMPTY_SEVERITY)["records"])


def _render_overview() -> None:
    stats = _load_stats()
    if stats is None:
        return
    _panel_overview(
        stats,
        pd.DataFrame(stats["per_family"]),
        _severity_frame(),
        set(stats["documented_families"]),
    )


def _render_severity() -> None:
    stats = _load_stats()
    if stats is None:
        return
    sev_df = _severity_frame()
    if sev_df.empty:
        st.info("Análise de severidade indisponível — verifique a API e a ingestão.")
        return
    _panel_severity(sev_df, set(stats["documented_families"]), _filter_row(stats, sev_df))


def _render_signatures() -> None:
    stats = _load_stats()
    if stats is None:
        return
    selection = _filter_row(stats, _severity_frame())
    _panel_signatures(_safe_get("/analytics/signatures", _EMPTY_SIGNATURES), selection)


def _filter_row(stats: dict, sev_df: pd.DataFrame) -> dict:
    """Filtros na própria página, em uma linha acima dos gráficos.

    As chaves são compartilhadas entre os painéis de Severidade e
    Assinaturas — como só um renderiza por vez, a seleção é preservada
    ao alternar entre eles.
    """
    per_family = pd.DataFrame(stats["per_family"])
    fault_names = sorted(per_family.loc[per_family["is_fault"], "family"])
    rpms = sorted(sev_df["rpm"].unique().tolist()) if not sev_df.empty else []

    left, right = st.columns([3, 2])
    families = left.multiselect(
        "Famílias de falha", fault_names, default=fault_names, key="flt_families"
    )
    chosen_rpms = right.multiselect(
        "Regimes de rotação",
        rpms,
        default=rpms,
        format_func=lambda r: f"{int(r)} rpm",
        key="flt_rpms",
    )
    return {"families": families or fault_names, "rpms": chosen_rpms or rpms}


# --------------------------------------------------------------------------
# 1. Visão geral
# --------------------------------------------------------------------------
def _panel_overview(
    stats: dict, per_family: pd.DataFrame, sev_df: pd.DataFrame, documented: set[str]
) -> None:
    """Painel "Visão geral": métricas resumo, matriz de priorização (frequência ×
    severidade × cobertura), ranking de ocorrências por família e a linha do tempo."""
    faults = per_family[per_family["is_fault"]]
    total = int(per_family["count"].sum())
    n_fault = int(faults["count"].sum())
    covered = len(documented & set(faults["family"]))

    monthly = pd.DataFrame(stats["monthly_faults"])
    window = f"{monthly['month'].min()} → {monthly['month'].max()}" if not monthly.empty else "—"

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Eventos monitorados", f"{total:,}".replace(",", "."))
    c2.metric("Eventos de falha", f"{n_fault / total:.0%}", f"{n_fault:,}".replace(",", "."))
    c3.metric("Famílias de falha", len(faults))
    c4.metric(
        "Cobertura documental",
        f"{covered}/{len(faults)}",
        f"{covered / len(faults):.0%}" if len(faults) else None,
    )
    c5.metric("Janela", window)

    st.subheader("Priorização: frequência × severidade × cobertura documental")
    st.caption(
        "Cada bolha é uma família de falha. Quanto mais à direita e mais acima, maior a "
        "urgência; em laranja, as que **ainda não têm procedimento** cadastrado — o canto "
        "superior direito laranja é o que deve ser documentado primeiro."
    )
    _priority_matrix(faults, sev_df, documented)

    left, right = st.columns([1, 1])
    with left:
        _bar_occurrences(faults, documented)
    with right:
        _timeline(monthly)

    with st.expander("Estados operacionais (não são falha)"):
        states = per_family[~per_family["is_fault"]][["family", "count"]]
        st.dataframe(states, use_container_width=True, hide_index=True)


def _priority_matrix(faults: pd.DataFrame, sev_df: pd.DataFrame, documented: set[str]) -> None:
    """Gráfico de bolhas: eixo X = quantidade de ocorrências, eixo Y = severidade
    máxima relativa à linha de base. Cor indica se a família já tem documento."""
    if sev_df.empty:
        st.info("Análise de severidade indisponível.")
        return

    worst = (
        sev_df[sev_df["is_fault"]]
        .groupby("family", as_index=False)["relative_severity"]
        .max()
        .rename(columns={"relative_severity": "severidade"})
    )
    df = faults.merge(worst, on="family", how="left").dropna(subset=["severidade"])
    if df.empty:
        st.info("Sem dados suficientes para a matriz de priorização.")
        return
    df["documentada"] = df["family"].map(lambda f: f in documented)

    p = palette()
    fig = go.Figure()
    for is_doc, label, color in (
        (True, "✓ documentada", p.documented),
        (False, "✗ sem documento", p.undocumented),
    ):
        part = df[df["documentada"] == is_doc]
        if part.empty:
            continue
        fig.add_trace(
            go.Scatter(
                x=part["count"],
                y=part["severidade"],
                mode="markers+text" if not is_doc else "markers",
                text=part["family"] if not is_doc else None,
                textposition="top center",
                textfont={"color": p.text_secondary, "size": 11},
                name=label,
                marker={
                    "size": 22,
                    "color": color,
                    "line": {"width": 2, "color": p.surface},
                    "opacity": 0.9,
                },
                customdata=part[["family"]],
                hovertemplate=(
                    "<b>%{customdata[0]}</b><br>ocorrências: %{x:,}<br>"
                    "severidade máx: %{y:.2f}× a linha de base<extra></extra>"
                ),
            )
        )
    fig.add_hline(y=df["severidade"].median(), line_width=1, line_dash="dot", line_color=p.axis)
    fig.add_vline(x=df["count"].median(), line_width=1, line_dash="dot", line_color=p.axis)
    fig.update_layout(
        xaxis_title="ocorrências registradas",
        yaxis_title="severidade máxima (× linha de base do mesmo RPM)",
    )
    st.plotly_chart(style(fig, height=440), use_container_width=True, theme=None)


def _bar_occurrences(faults: pd.DataFrame, documented: set[str]) -> None:
    """Gráfico de barras horizontais com o total de ocorrências por família de falha."""
    p = palette()
    df = faults.sort_values("count")
    colors = [p.documented if f in documented else p.undocumented for f in df["family"]]
    fig = go.Figure(
        go.Bar(
            x=df["count"],
            y=df["family"],
            orientation="h",
            marker={"color": colors, "cornerradius": 4},
            hovertemplate="<b>%{y}</b><br>%{x:,} ocorrências<extra></extra>",
        )
    )
    fig.update_layout(title="Ocorrências por família", xaxis_title="eventos", bargap=0.35)
    st.plotly_chart(style(fig, height=420, legend=False), use_container_width=True, theme=None)
    st.caption("Azul = possui documento orientativo · laranja = sem documento.")


def _timeline(monthly: pd.DataFrame) -> None:
    """Gráfico de linha com o total de eventos de falha por mês."""
    if monthly.empty:
        st.info("Sem série temporal disponível.")
        return
    agg = monthly.groupby("month", as_index=False)["count"].sum()
    p = palette()
    fig = go.Figure(
        go.Scatter(
            x=agg["month"],
            y=agg["count"],
            mode="lines+markers",
            line={"width": 2, "color": p.series[0]},
            marker={"size": 8, "color": p.series[0]},
            fill="tozeroy",
            fillcolor=_alpha(p.series[0], 0.15),
            hovertemplate="<b>%{x}</b><br>%{y:,} eventos de falha<extra></extra>",
        )
    )
    fig.update_layout(title="Eventos de falha por mês", yaxis_title="eventos")
    st.plotly_chart(style(fig, height=420, legend=False), use_container_width=True, theme=None)


# --------------------------------------------------------------------------
# 2. Severidade & Física
# --------------------------------------------------------------------------
def _panel_severity(sev_df: pd.DataFrame, documented: set[str], selection: dict) -> None:
    """Painel "Severidade & Física": permite alternar entre escala absoluta
    (zonas ISO 10816) e relativa à linha de base, além do gráfico de validação física."""
    if sev_df.empty:
        st.info("Análise de severidade indisponível — verifique a API e a ingestão.")
        return

    st.caption(
        "O RMS mediano é quase idêntico entre as famílias quando os regimes de rotação são "
        "misturados (2,2–2,8 mm/s). Por isso **toda leitura de severidade aqui é estratificada "
        "por RPM** — comparar sem condicionar à rotação leva a conclusão errada."
    )

    col1, col2 = st.columns([1, 2])
    machine_class = col1.selectbox(
        "Classe ISO 10816", list(ISO_10816_ZONES), index=list(ISO_10816_ZONES).index(DEFAULT_CLASS)
    )
    mode = col2.radio(
        "Escala",
        ["Absoluta (mm/s, zonas ISO)", "Relativa à linha de base do mesmo RPM"],
        horizontal=True,
    )
    absolute = mode.startswith("Absoluta")

    df = sev_df[sev_df["is_fault"] & sev_df["family"].isin(selection["families"])]
    df = df[df["rpm"].isin(selection["rpms"])]
    if df.empty:
        st.info("Nenhum dado para o filtro atual.")
        return

    _severity_bars(df, machine_class, absolute)
    st.divider()
    _physics_chart(sev_df, selection)
    with st.expander("Tabela — zona ISO por família e regime"):
        table = df[["family", "rpm", "n", "x_p50", "x_p95", "relative_severity"]].copy()
        table["zona ISO"] = table["x_p50"].map(lambda v: classify_zone(v, machine_class))
        table["rpm"] = table["rpm"].astype(int)
        st.dataframe(
            table.rename(
                columns={
                    "family": "família",
                    "n": "eventos",
                    "x_p50": "RMS p50 (mm/s)",
                    "x_p95": "RMS p95 (mm/s)",
                    "relative_severity": "× linha de base",
                }
            ).sort_values(["família", "rpm"]),
            use_container_width=True,
            hide_index=True,
        )


def _severity_bars(df: pd.DataFrame, machine_class: str, absolute: bool) -> None:
    """Barras horizontais de severidade por família, agrupadas por regime de RPM.
    Em modo absoluto desenha também as faixas das zonas ISO 10816 de fundo."""
    p = palette()
    value_col = "x_p50" if absolute else "relative_severity"
    rpms = sorted(df["rpm"].unique())
    colors = rpm_color_map(rpms)

    fig = go.Figure()
    for rpm in rpms:
        part = df[df["rpm"] == rpm].sort_values("family")
        fig.add_trace(
            go.Bar(
                x=part[value_col],
                y=part["family"],
                orientation="h",
                name=f"{int(rpm)} rpm",
                marker={
                    "color": colors[rpm],
                    "cornerradius": 4,
                    "line": {"width": 2, "color": p.surface},
                },
                hovertemplate=(
                    f"<b>%{{y}}</b> · {int(rpm)} rpm<br>"
                    + ("RMS: %{x:.2f} mm/s" if absolute else "%{x:.2f}× a linha de base")
                    + "<extra></extra>"
                ),
            )
        )

    if absolute:
        ab, bc, cd = ISO_10816_ZONES[machine_class]
        bands = [(0, ab, "A"), (ab, bc, "B"), (bc, cd, "C"), (cd, max(df[value_col]) * 1.15, "D")]
        for lo, hi, zone in bands:
            if hi <= lo:
                continue
            fig.add_vrect(
                x0=lo,
                x1=hi,
                fillcolor=p.muted,
                opacity=0.06 if zone in ("A", "C") else 0.11,
                line_width=0,
                layer="below",
                annotation_text=f"zona {zone}",
                annotation_position="top left",
                annotation_font={"color": p.muted, "size": 11},
            )
    else:
        fig.add_vline(x=1.0, line_width=1, line_dash="dot", line_color=p.axis)

    fig.update_layout(
        title=(
            f"Velocidade RMS mediana por família e rotação — zonas ISO 10816 classe {machine_class}"
            if absolute
            else "Severidade relativa à linha de base (estado normal no mesmo RPM)"
        ),
        xaxis_title="mm/s" if absolute else "× linha de base",
        barmode="group",
        bargap=0.3,
        bargroupgap=0.05,
    )
    st.plotly_chart(
        style(fig, height=max(420, 46 * df["family"].nunique())),
        use_container_width=True,
        theme=None,
    )

    if absolute:
        legend = " · ".join(f"**{z}** {ZONE_MEANING[z]}" for z in "ABCD")
        st.caption(
            f"{legend}. A classe ISO depende da potência e da fundação da máquina — ajuste-a "
            "acima. Este dataset vem de bancada com falhas induzidas, então as zonas absolutas "
            "são indicativas; para decidir intervenção, use a escala relativa."
        )


def _physics_chart(sev_df: pd.DataFrame, selection: dict) -> None:
    st.subheader("Validação física: a vibração cresce com o quadrado da rotação?")
    st.caption(
        "O Doc3 define a força do desbalanceamento como **F = m·r·ω²**. Se os dados forem "
        "coerentes com a física dos manuais, o desbalanceamento deve disparar nas rotações "
        "altas enquanto as demais falhas permanecem estáveis."
    )
    p = palette()
    df = sev_df[sev_df["is_fault"]].sort_values("rpm")
    highlight = [f for f in selection["families"] if f in ("desbalanceamento",)] or [
        selection["families"][0]
    ]

    fig = go.Figure()
    for family, part in df.groupby("family"):
        if family in highlight:
            continue
        fig.add_trace(
            go.Scatter(
                x=part["rpm"],
                y=part["x_p50"],
                mode="lines",
                name=family,
                line={"width": 1.5, "color": p.muted},
                opacity=0.5,
                showlegend=False,
                hovertemplate=f"<b>{family}</b><br>%{{x:.0f}} rpm · %{{y:.2f}} mm/s<extra></extra>",
            )
        )
    for i, family in enumerate(highlight):
        part = df[df["family"] == family]
        fig.add_trace(
            go.Scatter(
                x=part["rpm"],
                y=part["x_p50"],
                mode="lines+markers+text",
                name=family,
                text=[f"{v:.2f}" for v in part["x_p50"]],
                textposition="top center",
                textfont={"color": p.text_secondary, "size": 11},
                line={"width": 3, "color": p.series[i % len(p.series)]},
                marker={"size": 10, "line": {"width": 2, "color": p.surface}},
                hovertemplate=f"<b>{family}</b><br>%{{x:.0f}} rpm · %{{y:.2f}} mm/s<extra></extra>",
            )
        )
    fig.update_layout(
        title="Velocidade RMS mediana × rotação",
        xaxis_title="rotação (rpm)",
        yaxis_title="RMS (mm/s)",
    )
    st.plotly_chart(style(fig, height=420), use_container_width=True, theme=None)
    st.caption(
        "Em cinza, todas as demais famílias de falha (referência). A curva destacada confirma "
        "a previsão do manual: o desbalanceamento sai de ~2,5 mm/s nas rotações baixas para "
        "**7,50 mm/s a 3000 rpm** — 2,65× a linha de base —, enquanto as outras falhas seguem "
        "praticamente planas."
    )


# --------------------------------------------------------------------------
# 3. Assinaturas
# --------------------------------------------------------------------------
def _panel_signatures(signatures: dict, selection: dict) -> None:
    """Painel "Assinaturas": mapa de calor de z-scores por família e feature,
    seguido do ranking de poder discriminativo de cada indicador."""
    rows = signatures.get("signatures", [])
    if not rows:
        st.info("Assinaturas indisponíveis — verifique a API.")
        return

    st.caption(
        "Cada célula é o desvio médio da família em relação à média global daquele indicador "
        "(z-score). Azul = abaixo da média, vermelho = acima. Serve para ler **o que "
        "caracteriza** cada falha — e para expor o que não caracteriza nada."
    )

    keep = set(selection["families"]) | {"normal"}
    data = [r for r in rows if r["family"] in keep]
    if not data:
        st.info("Nenhuma família no filtro atual.")
        return

    order = [d["feature"] for d in signatures.get("discriminative_power", [])]
    features = order or list(data[0]["z_scores"])
    matrix = [[r["z_scores"].get(f) for f in features] for r in data]

    fig = go.Figure(
        go.Heatmap(
            z=matrix,
            x=[_label(f) for f in features],
            y=[r["family"] for r in data],
            colorscale=diverging_scale(),
            zmid=0,
            zmin=-2,
            zmax=2,
            colorbar={"title": "z-score", "thickness": 12},
            hovertemplate="<b>%{y}</b><br>%{x}: z = %{z:.2f}<extra></extra>",
            xgap=2,
            ygap=2,
        )
    )
    fig.update_layout(
        title="Assinatura das falhas (indicadores ordenados por poder discriminativo)"
    )
    fig.update_xaxes(tickangle=-45)
    st.plotly_chart(
        style(fig, height=max(380, 34 * len(data) + 180), legend=False),
        use_container_width=True,
        theme=None,
    )

    st.subheader("Quais indicadores realmente separam as falhas?")
    _discriminative_bars(signatures.get("discriminative_power", []))


def _discriminative_bars(power: list[dict]) -> None:
    """Barras horizontais do poder discriminativo (razão F) de cada feature,
    destacando em cinza as quatro que menos separam as famílias de falha."""
    if not power:
        return
    p = palette()
    df = pd.DataFrame(power).dropna(subset=["f_ratio"]).sort_values("f_ratio")
    weakest = set(df.head(4)["feature"])
    colors = [p.muted if f in weakest else p.series[0] for f in df["feature"]]

    fig = go.Figure(
        go.Bar(
            x=df["f_ratio"],
            y=[_label(f) for f in df["feature"]],
            orientation="h",
            marker={"color": colors, "cornerradius": 4},
            hovertemplate="<b>%{y}</b><br>razão F = %{x:,.0f}<extra></extra>",
        )
    )
    fig.update_layout(
        title="Poder discriminativo (variância entre famílias ÷ variância dentro da família)",
        xaxis_title="razão F — maior separa melhor",
        bargap=0.3,
    )
    st.plotly_chart(style(fig, height=520, legend=False), use_container_width=True, theme=None)

    weak_labels = ", ".join(sorted(_label(f) for f in weakest))
    st.caption(
        f"Em cinza, os indicadores de menor poder discriminativo ({weak_labels}). É um achado "
        "relevante: os manuais associam impactos de rolamento a **curtose** e **fator de crista** "
        "elevados, mas nestes dados essas métricas são praticamente idênticas entre todas as "
        "famílias (curtose 2,65–2,75; crista 3,73–3,85), ou seja, não sustentam o diagnóstico "
        "sozinhas."
    )


# --------------------------------------------------------------------------
# 4. Qualidade do modelo
# --------------------------------------------------------------------------
def _panel_model_quality(quality: dict) -> None:
    """Painel "Qualidade do modelo": compara os três protocolos de avaliação do
    treino e expõe o diagnóstico de vazamento de dados por sessão de coleta."""
    metrics = quality.get("metrics") or {}
    if not metrics:
        st.info("Métricas indisponíveis — execute `python -m src.ml.train`.")
        return

    evals = {
        "Holdout por sessão": metrics.get("eval_session_holdout"),
        "Split aleatório": metrics.get("eval_random_split"),
        "Holdout sem temperatura": metrics.get("eval_session_holdout_no_temp"),
    }
    evals = {k: v for k, v in evals.items() if v}

    cols = st.columns(len(evals))
    for col, (name, ev) in zip(cols, evals.items(), strict=True):
        col.metric(name, f"F1 {ev['f1_macro']:.3f}", f"acurácia {ev['accuracy']:.3f}")

    st.caption(
        "**Como ler:** o *split aleatório* mistura leituras da mesma campanha de coleta entre "
        "treino e teste — leituras quase idênticas — e por isso superestima o desempenho. O "
        "*holdout por sessão* separa campanhas inteiras e mede o que interessa em produção: "
        "reconhecer a falha em uma coleta nova. A diferença entre os dois não é ruído, é o "
        "efeito do vazamento diagnosticado abaixo."
    )

    _f1_by_class(evals)
    st.divider()
    st.subheader("Diagnóstico do vazamento: a temperatura carimba a campanha de coleta")
    left, right = st.columns(2)
    with left:
        _feature_importance(quality.get("feature_importance", []))
    with right:
        _leakage_bars(quality.get("leakage", {}))

    leak = quality.get("leakage", {}).get("features", [])
    temp = next((f for f in leak if f["feature"] == "temperature_c"), None)
    confounded = [f for f in leak if (f.get("ratio") or 0) > 1]
    if temp:
        st.info(
            f"**O que a medição mostra.** A temperatura é a feature mais influente do "
            f"classificador, mas varia **{temp['ratio']}×** mais entre campanhas de coleta "
            f"(desvio {temp['between_sessions_sd']} °C) do que dentro de uma mesma campanha "
            f"(desvio {temp['within_session_sd']} °C): ela funciona como relógio da campanha, "
            "não como sintoma mecânico."
        )
    if len(confounded) > 1:
        no_temp = (quality.get("metrics") or {}).get("eval_session_holdout_no_temp")
        base = (quality.get("metrics") or {}).get("eval_session_holdout")
        ablation = ""
        if no_temp and base:
            ablation = (
                f" Testamos remover a temperatura do vetor de entrada: o F1 do holdout por "
                f"sessão **caiu** de {base['f1_macro']:.3f} para {no_temp['f1_macro']:.3f} — "
                "ou seja, a hipótese de que ela *causa* o gap está errada."
            )
        st.warning(
            f"**Conclusão revista — o vazamento é sistêmico, não de uma feature.**{ablation} "
            f"Aplicando a mesma decomposição de variância a todo o vetor, "
            f"**{len(confounded)} de {len(leak)} features** têm dispersão maior entre campanhas "
            "do que dentro delas. Retirar a mais extrema só faz o modelo migrar para a próxima "
            "proxy. A causa está no desenho experimental: cada condição foi coletada em uma "
            "única campanha, o que confunde sessão com rótulo em todo o espaço de features — "
            "nenhuma seleção de features corrige isso. **Correções reais:** coletar múltiplas "
            "campanhas por condição; normalizar contra a linha de base da própria máquina em "
            "vez de usar valores absolutos; e, no produto, priorizar a **busca por similaridade** "
            "(que mostra ocorrências próximas e deixa a decisão com o técnico) sobre a "
            "afirmação categórica de uma classe."
        )


def _f1_by_class(evals: dict) -> None:
    """Compara o F1 por classe entre os diferentes protocolos de avaliação do modelo."""
    p = palette()
    fig = go.Figure()
    for i, (name, ev) in enumerate(evals.items()):
        per_class = ev.get("f1_per_class", {})
        if not per_class:
            continue
        items = sorted(per_class.items(), key=lambda kv: -kv[1])
        fig.add_trace(
            go.Bar(
                x=[v for _, v in items],
                y=[k for k, _ in items],
                orientation="h",
                name=name,
                marker={"color": p.series[i % len(p.series)], "cornerradius": 3},
                hovertemplate=f"<b>%{{y}}</b><br>{name}: F1 = %{{x:.3f}}<extra></extra>",
            )
        )
    fig.update_layout(
        title="F1 por classe — mesmo modelo, dois protocolos de avaliação",
        xaxis_title="F1",
        barmode="group",
        bargap=0.25,
    )
    st.plotly_chart(style(fig, height=560), use_container_width=True, theme=None)


def _feature_importance(importance: list[dict]) -> None:
    """Barras com as 12 features mais importantes para o LightGBM (a suspeita
    de vazamento aparece destacada em vermelho)."""
    if not importance:
        return
    p = palette()
    df = pd.DataFrame(importance).head(12).sort_values("importance_pct")
    colors = [p.alert if f == "temperature_c" else p.series[0] for f in df["feature"]]
    fig = go.Figure(
        go.Bar(
            x=df["importance_pct"],
            y=[_label(f) for f in df["feature"]],
            orientation="h",
            marker={"color": colors, "cornerradius": 4},
            hovertemplate="<b>%{y}</b><br>%{x:.1f}% da importância<extra></extra>",
        )
    )
    fig.update_layout(
        title="Importância no LightGBM (top 12)", xaxis_title="% da importância", bargap=0.3
    )
    st.plotly_chart(style(fig, height=420, legend=False), use_container_width=True, theme=None)
    st.caption("Em vermelho, a feature sinalizada como vazamento.")


def _leakage_bars(leakage: dict) -> None:
    """Barras com a razão de dispersão entre campanhas ÷ dentro da campanha,
    por feature — valores altos indicam vazamento (a feature "identifica a sessão")."""
    features = leakage.get("features", [])
    if not features:
        return
    p = palette()
    df = pd.DataFrame(features).dropna(subset=["ratio"]).head(12).sort_values("ratio")
    colors = [p.alert if f == "temperature_c" else p.series[0] for f in df["feature"]]
    fig = go.Figure(
        go.Bar(
            x=df["ratio"],
            y=[_label(f) for f in df["feature"]],
            orientation="h",
            marker={"color": colors, "cornerradius": 4},
            hovertemplate=(
                "<b>%{y}</b><br>dispersão entre campanhas ÷ dentro da campanha = "
                "%{x:.1f}×<extra></extra>"
            ),
        )
    )
    fig.add_vline(x=1.0, line_width=1, line_dash="dot", line_color=p.axis)
    fig.update_layout(
        title="Vazamento por sessão (dispersão entre ÷ dentro das campanhas)",
        xaxis_title="razão — acima de 1× a feature identifica a campanha",
        bargap=0.3,
    )
    st.plotly_chart(style(fig, height=420, legend=False), use_container_width=True, theme=None)
    st.caption(f"Calculado sobre {leakage.get('n_sessions', 0)} campanhas de coleta.")


def _alpha(hex_color: str, alpha: float) -> str:
    """Converte uma cor hexadecimal (#rrggbb) em rgba(...) com a transparência informada."""
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i : i + 2], 16) for i in (0, 2, 4))
    return f"rgba({r},{g},{b},{alpha})"
