# Manutenção Prescritiva — Máquinas Rotativas

Solução completa de **manutenção prescritiva** para a prova do processo seletivo Dev Full Stack I.A. e Python (SENAI SC): dado um novo evento de sensores de vibração, o sistema identifica o tipo de defeito, quantifica ocorrências históricas similares e gera **instruções de correção** via RAG sobre a base documental — restringindo-se apenas a falhas documentadas (falha sem documento → aviso + sugestão de registrar um novo documento).

📐 A arquitetura completa, com diagramas e ADRs, está em [ARCHITECTURE.md](ARCHITECTURE.md).
📊 A análise exploratória que fundamenta o dashboard — hipóteses, consultas SQL, achados e limitações — está em [ANALYTICS.md](ANALYTICS.md).
🚀 O provisionamento em nuvem (Railway, infraestrutura como código) está em [DEPLOY.md](DEPLOY.md).

## Stack

| Camada | Tecnologia |
|---|---|
| Diagnóstico | LightGBM (tipo de defeito + probabilidade) + KNN (ocorrências similares) |
| RAG | ChromaDB + `gemini-embedding-2` (Vertex AI, dim 768) |
| Geração | `gemini-3.6-flash` (Vertex AI), contexto fechado + citações determinísticas |
| Chat | Agente **Google ADK** com 4 ferramentas (histórico SQL, documentos c/ guardrail, diagnóstico, cobertura) e memória de sessão no Postgres; fallback automático p/ RAG direto |
| Backend | FastAPI |
| Frontend | Streamlit + Plotly |
| Histórico | PostgreSQL (Docker) |
| Ingestão industrial | MQTT (Eclipse Mosquitto + paho-mqtt), com DLQ e tópico de alertas |
| CI/CD | GitHub Actions (ruff → pytest → docker build) |

## Setup rápido

Pré-requisitos: Python 3.12+, Docker, projeto GCP com Vertex AI habilitado.

```bash
# 1. Ambiente
python -m venv .venv
.venv\Scripts\activate           # Windows  (Linux/Mac: source .venv/bin/activate)
pip install -e .[dev]

# 2. Configuração
copy .env.example .env           # e preencha GOOGLE_CLOUD_PROJECT etc.
gcloud auth application-default login

# 3. Infra (Postgres + broker MQTT)
docker compose up -d postgres mosquitto

# 4. Dados: banner.csv → Postgres (166.796 eventos, 151 rótulos → 17 famílias)
python scripts/ingest_data.py --csv documentos/banner.csv

# 5. Treino do motor de diagnóstico (scaler + KNN + LightGBM → artifacts/)
python -m src.ml.train

# 6. Base documental: Doc1..Doc6 → ChromaDB (requer credenciais Vertex AI)
python scripts/ingest_docs.py --docs-dir documentos

# 7. Serviços
uvicorn src.api.main:app --port 8000           # API      → http://localhost:8000/docs
streamlit run src/ui/app.py                    # UI       → http://localhost:8501
python -m src.ingestion.mqtt_worker            # worker MQTT (opcional)
```

Alternativa tudo-em-Docker: `docker compose up --build` (após os passos 4–6).

## Demonstração

**Diagnóstico via API** com o JSON de exemplo do edital:

```bash
curl -X POST http://localhost:8000/api/v1/diagnose -H "Content-Type: application/json" -d @evento.json
```

Resposta: família prevista (`cocked_rotor`, prob. 92%), 14.275 ocorrências similares com distribuição temporal e frequência/dia, e as instruções de correção citando o Doc6 (ou, se a família não tiver documento, o aviso + sugestão de registro).

**Simulador de sensor via MQTT** (replay do dataset — o dashboard atualiza sozinho):

```bash
python scripts/simulate_sensor.py --family cocked_rotor --limit 5 --rate 2
python scripts/simulate_sensor.py --invalid      # demonstra a dead-letter queue
```

O worker valida cada payload (mesmo schema pydantic da API), persiste no Postgres, dispara o diagnóstico e publica alertas em `sensors/{maquina}/alerts` — payloads inválidos vão para `sensors/{maquina}/dlq` com o motivo.

**Chat com agente (Google ADK)** — exemplos de perguntas que exercitam as ferramentas:

- "Quantas ocorrências de `cocked_rotor` nos últimos 30 dias?" → `consultar_historico` (SQL no Postgres)
- "Como corrijo um desalinhamento?" → `buscar_documentos` (RAG com citações doc/seção/página)
- "Como corrijo a ventoinha?" → guardrail: aviso "não documentado" + sugestão de registro
- "Quais falhas têm procedimento documentado?" → `cobertura_documental`
- Colar o JSON de um evento → `diagnosticar_evento` (KNN + LightGBM) com explicação

A memória da conversa fica no Postgres (sessões do ADK); sem credenciais GCP o chat cai automaticamente no RAG de um passo (badge na UI indica o modo).

**Fluxo "problema não documentado"**: as famílias `eccentric_rotor`, `ventoinha` e `falta_fase` existem no histórico mas não têm documento orientativo. Um diagnóstico dessas famílias retorna o aviso e sugere o registro; ao cadastrar um PDF na aba **Documentos** da UI (ou via `POST /api/v1/documents`), a família passa a ser coberta imediatamente.

## Testes e qualidade

```bash
pytest          # 63 testes — offline (FakeLLM + SQLite + artefatos sintéticos);
                # os de analytics usam schema temporário no Postgres e são pulados sem ele
ruff check src tests scripts
```

Nenhum teste chama o Vertex AI: o cliente LLM é uma interface fina substituída por um fake determinístico nos testes e no CI.

## Métricas do modelo

Registradas em `artifacts/metrics.json` a cada treino, em **três protocolos**: holdout por sessão de coleta (métrica principal e honesta), split aleatório estratificado (referência otimista) e holdout por sessão sem a temperatura (teste de ablação do vazamento). O dashboard mostra os três lado a lado com a evidência do confundimento sessão↔rótulo — análise completa em [ANALYTICS.md](ANALYTICS.md) §4.

## Estrutura

```
src/core       config, schemas pydantic (únicos p/ API, MQTT e ETL), modelos SQLAlchemy
src/etl        canonicalização auditável de rótulos (151 rótulos brutos → 17 famílias)
src/ml         features físicas, treino, diagnóstico KNN + LightGBM
src/rag        chunking de PDFs, ChromaDB, guardrail de cobertura, orquestração RAG
src/llm        cliente Vertex AI + prompts anti-alucinação
src/api        FastAPI (diagnose, chat, stats, events, documents, health)
src/ingestion  worker MQTT (QoS 1, idempotência, DLQ, alertas)
src/ui         Streamlit — dashboard analítico (4 sub-abas), diagnóstico, chat, documentos
               + theme.py (paleta validada p/ daltonismo) e api_client.py
scripts        ingest_data, ingest_docs, simulate_sensor
tests          63 testes
```
