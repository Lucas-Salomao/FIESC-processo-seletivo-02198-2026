"""Frontend Streamlit — consome exclusivamente a API FastAPI.

Navegação na barra lateral: Dashboard (com 4 painéis), Diagnóstico, Chat
e Documentos. Só a seção ativa é renderizada, então cada painel busca apenas
os dados de que precisa.

Execução local:  streamlit run src/ui/app.py
"""

import json
import uuid
from pathlib import Path

import pandas as pd
import plotly.express as px
import requests
import streamlit as st

from src.ui import dashboard
from src.ui.api_client import API, API_URL, api_get

st.set_page_config(
    page_title="Manutenção Prescritiva — SENAI SC",
    page_icon="🛠️",
    layout="wide",
    menu_items={
        "About": """
    ### Aplicação de Manutenção Prescritiva
    Desenvolvido por: Lucas Tadeu Monteiro Guedes Fernandes Salomão
    """
    },
)

# Caminho absoluto: o app pode ser iniciado de qualquer diretório.
_LOGO = Path(__file__).resolve().parents[2] / "images" / "logo.png"
if _LOGO.exists():
    st.logo(str(_LOGO), size="large")

EXAMPLE_EVENT = {
    "id": 114387,
    "created_at": "2026-06-01 21:32:53.911176+00:00",
    "z_rms_velocity_mm_s": 1.517,
    "temperature_c": 24.69,
    "x_rms_velocity_mm_s": 2.0,
    "z_peak_acceleration_g": 0.484,
    "x_peak_acceleration_g": 0.631,
    "z_peak_vel_comp_freq_hz": 61.0,
    "x_peak_vel_comp_freq_hz": 61.0,
    "z_rms_acceleration_g": 0.09,
    "x_rms_acceleration_g": 0.114,
    "z_kurtosis": 2.392,
    "x_kurtosis": 2.77,
    "z_crest_factor": 3.747,
    "x_crest_factor": 4.269,
    "z_peak_velocity_mm_s": 2.146,
    "x_peak_velocity_mm_s": 2.829,
    "z_high_freq_rms_accel_g": 0.129,
    "x_high_freq_rms_accel_g": 0.147,
    "fault": "cocked_rotor_2",
    "rpm": 1000.0,
}


SECTION_DASHBOARD = "Dashboard"
SECTIONS = {
    SECTION_DASHBOARD: "📊",
    "Diagnóstico": "🔎",
    "Chat": "💬",
    "Documentos": "📄",
}
DASHBOARD_PANELS = ("Visão geral", "Severidade & Física", "Assinaturas", "Qualidade do modelo")


def render_sidebar() -> tuple[str, str | None]:
    """Navegação lateral. Devolve (seção, painel) — painel só para o Dashboard."""
    with st.sidebar:
        st.markdown("#### Manutenção Prescritiva")
        section = st.radio(
            "Seção",
            list(SECTIONS),
            format_func=lambda s: f"{SECTIONS[s]}  {s}",
            label_visibility="collapsed",
            key="nav_section",
        )

        panel = None
        if section == SECTION_DASHBOARD:
            _, items = st.columns([1, 11])  # recuo visual do subnível
            with items:
                panel = st.radio(
                    "Painel",
                    DASHBOARD_PANELS,
                    label_visibility="collapsed",
                    key="nav_panel",
                )

        st.divider()
        _render_status()
    return section, panel


def _render_status() -> None:
    """Estado do sistema — recolhido por padrão para não competir com a navegação."""
    try:
        health = api_get("/health")
    except requests.RequestException:
        st.error(f"API indisponível em {API_URL}")
        st.stop()

    ok = health["artifacts_loaded"]
    with st.expander("🟢 Sistema online" if ok else "🟡 Sistema parcial", expanded=False):
        st.caption(f"Modelo LLM: `{health['chat_model']}`")
        st.caption(f"Embeddings: `{health['embedding_model']}`")
        st.caption(f"Artefatos ML: {'✅' if ok else '❌ execute `python -m src.ml.train`'}")
        st.caption(f"Famílias documentadas: {len(health['documented_families'])}")


def render_diagnosis(data: dict) -> None:
    conf_color = {"alta": "green", "media": "orange", "baixa": "red"}[data["confidence"]]
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Tipo de defeito", data["predicted_fault"])
    col2.metric("Probabilidade", f"{data['probability']:.1%}")
    col3.metric("Concordância KNN", f"{data['knn_agreement']:.1%}")
    col4.markdown(f"**Confiança**\n\n:{conf_color}[{data['confidence'].upper()}]")

    if not data["is_fault"]:
        st.success("Estado operacional — não é uma falha. Nenhuma ação prescritiva necessária.")
        return

    sim = data["similar_events"]
    st.subheader("Ocorrências históricas similares")
    c1, c2, c3 = st.columns(3)
    c1.metric("Total registrado", f"{sim['count']:,}")
    c2.metric("Frequência", f"{sim['freq_per_day']}/dia" if sim["freq_per_day"] else "—")
    period = "—"
    if sim["first_seen"]:
        period = f"{sim['first_seen'][:10]} → {sim['last_seen'][:10]}"
    c3.metric("Período", period)

    timeline = pd.DataFrame(sim["timeline"])
    if not timeline.empty:
        fig = px.bar(
            timeline,
            x="month",
            y="count",
            title="Distribuição das ocorrências similares ao longo do tempo",
        )
        st.plotly_chart(fig, use_container_width=True)

    if data["documented"] and data["prescription"]:
        st.subheader("📋 Instruções de correção (RAG sobre a base documental)")
        st.markdown(data["prescription"]["instructions_md"])
        with st.expander("Fontes citadas"):
            for cit in data["prescription"]["citations"]:
                st.markdown(f"- **{cit['doc']}** — {cit['section'] or ''} (p. {cit['page']})")
    else:
        st.error("⚠️ Problema ainda não documentado")
        st.markdown(data["suggestion"] or "")


def tab_diagnose() -> None:
    st.header("Diagnóstico de novo evento")
    st.caption("Cole o JSON do evento (formato do enunciado) ou use o exemplo.")

    if st.button("Usar JSON de exemplo do edital"):
        st.session_state["event_json"] = json.dumps(EXAMPLE_EVENT, indent=2, ensure_ascii=False)

    event_json = st.text_area(
        "JSON do evento",
        key="event_json",
        height=260,
        placeholder=json.dumps({"z_rms_velocity_mm_s": 1.5, "...": "..."}, indent=2),
    )

    if st.button("Diagnosticar", type="primary"):
        try:
            payload = json.loads(event_json)
        except json.JSONDecodeError as exc:
            st.error(f"JSON inválido: {exc}")
            return
        with st.spinner("Executando diagnóstico + RAG..."):
            response = requests.post(f"{API}/diagnose", json=payload, timeout=180)
        if response.status_code != 200:
            st.error(f"Erro {response.status_code}: {response.text}")
            return
        data = response.json()
        st.session_state["last_diagnosis"] = data
        render_diagnosis(data)
    elif "last_diagnosis" in st.session_state:
        render_diagnosis(st.session_state["last_diagnosis"])


SUGGESTED_QUESTIONS = [
    "Quantas ocorrências de cocked_rotor nos últimos 30 dias?",
    "Como corrijo um desalinhamento?",
    "Quais falhas têm procedimento documentado?",
    "Como corrijo a ventoinha?",
]


def tab_chat() -> None:
    st.header("Chat prescritivo")
    last = st.session_state.get("last_diagnosis")
    family = None
    if last and last.get("is_fault"):
        family = last["predicted_fault"]
        st.caption(f"Contexto do diagnóstico corrente: **{family}**")
    else:
        st.caption(
            "Pergunte sobre histórico de falhas, procedimentos de correção ou cole um JSON de evento."
        )

    if "chat_session_id" not in st.session_state:
        st.session_state["chat_session_id"] = str(uuid.uuid4())
    if "chat_history" not in st.session_state:
        st.session_state["chat_history"] = []

    if not st.session_state["chat_history"]:
        cols = st.columns(len(SUGGESTED_QUESTIONS))
        for col, suggestion in zip(cols, SUGGESTED_QUESTIONS, strict=True):
            if col.button(suggestion, key=f"sug_{suggestion[:20]}"):
                st.session_state["pending_question"] = suggestion

    for msg in st.session_state["chat_history"]:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    question = st.chat_input("Pergunte sobre histórico, correção, ou cole um JSON de evento...")
    if not question and "pending_question" in st.session_state:
        question = st.session_state.pop("pending_question")

    if question:
        st.session_state["chat_history"].append({"role": "user", "content": question})
        with st.chat_message("user"):
            st.markdown(question)
        with st.chat_message("assistant"), st.spinner("Consultando..."):
            response = requests.post(
                f"{API}/chat",
                json={
                    "message": question,
                    "fault_family": family,
                    "history": st.session_state["chat_history"][:-1],
                    "session_id": st.session_state["chat_session_id"],
                },
                timeout=300,
            )
            if response.status_code != 200:
                st.error(f"Erro {response.status_code}: {response.text}")
                return
            data = response.json()
            badge = (
                "🤖 agente ADK (ferramentas + memória)"
                if data.get("agent_used")
                else "📄 RAG direto (fallback)"
            )
            st.caption(badge)
            st.markdown(data["answer_md"])
            if data["citations"]:
                with st.expander("Fontes"):
                    for cit in data["citations"]:
                        st.markdown(
                            f"- **{cit['doc']}** — {cit['section'] or ''} (p. {cit['page']})"
                        )
        st.session_state["chat_history"].append({"role": "assistant", "content": data["answer_md"]})


def tab_documents() -> None:
    st.header("Base documental")
    try:
        docs = api_get("/documents")
        st.markdown(
            "**Famílias documentadas:** " + ", ".join(f"`{f}`" for f in docs["documented_families"])
        )
        if docs["documents"]:
            st.dataframe(pd.DataFrame(docs["documents"]), use_container_width=True, hide_index=True)
    except requests.RequestException as exc:
        st.warning(f"Não foi possível listar documentos: {exc}")

    st.subheader("Registrar novo documento orientativo")
    st.caption(
        "Quando uma falha ainda não possui documento, cadastre aqui o procedimento — "
        "a família passa a ser coberta imediatamente."
    )
    with st.form("upload_doc"):
        file = st.file_uploader("PDF do procedimento", type=["pdf"])
        title = st.text_input("Título do documento")
        families = st.text_input(
            "Famílias cobertas (separadas por vírgula)",
            placeholder="ex.: ventoinha, eccentric_rotor",
        )
        submitted = st.form_submit_button("Ingerir documento", type="primary")

    if submitted:
        if not (file and title and families):
            st.error("Preencha PDF, título e famílias.")
            return
        with st.spinner("Extraindo, gerando embeddings e indexando..."):
            response = requests.post(
                f"{API}/documents",
                files={"file": (file.name, file.getvalue(), "application/pdf")},
                data={"title": title, "families": families},
                timeout=300,
            )
        if response.status_code == 200:
            result = response.json()
            st.success(f"Documento indexado: {result['chunks']} chunks para {result['families']}.")
            api_get.clear()
        else:
            st.error(f"Erro {response.status_code}: {response.text}")


_section, _panel = render_sidebar()

if _section == SECTION_DASHBOARD:
    dashboard.render(_panel)
elif _section == "Diagnóstico":
    tab_diagnose()
elif _section == "Chat":
    tab_chat()
else:
    tab_documents()
