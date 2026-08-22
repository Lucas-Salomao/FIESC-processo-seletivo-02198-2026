# Arquitetura — Solução de Manutenção Prescritiva

> Prova SENAI SC — Dev Full Stack I.A. e Python. Dado um novo evento JSON de sensores de vibração, o sistema identifica o tipo de defeito, quantifica ocorrências históricas similares (quantidade, distribuição temporal, frequência) e gera **instruções de correção** via RAG sobre a base documental da empresa — restringindo-se **apenas a falhas documentadas**; caso contrário, informa que o problema ainda não está documentado e sugere o registro de um novo documento.

## 1. Visão geral

```mermaid
flowchart TB
    subgraph ENTRADA["Entrada"]
        SENS["Sensores IIoT / simulador<br/>(replay do dataset)"]
        EV["Novo evento JSON<br/>(REST direto)"]
        USR["Usuário<br/>(chat / novos documentos)"]
    end

    subgraph APP["Estação de Trabalho (docker-compose)"]
        MQTT["Broker MQTT<br/>Eclipse Mosquitto<br/>topic: sensors/+/telemetry"]
        WRK["Ingestion Worker<br/>(paho-mqtt → valida → Postgres<br/>→ dispara /diagnose → alerts)"]
        subgraph UI["Frontend — Streamlit"]
            DASH["Dashboard"]
            CHAT["Chat prescritivo"]
            UPL["Upload de documentos"]
        end
        subgraph API["Backend — FastAPI"]
            RT["/diagnose /chat /events<br/>/stats /documents /health"]
            DIAG["Motor de Diagnóstico<br/>KNN + LightGBM"]
            GUARD["Guardrail de cobertura<br/>documental (determinístico)"]
            RAGS["Serviço RAG<br/>(retrieve → prompt → cite)"]
        end
        PG[("PostgreSQL<br/>histórico de eventos")]
        CH[("ChromaDB<br/>chunks dos documentos")]
        ART["Artefatos ML<br/>scaler + knn + lgbm"]
    end

    subgraph GCP["Google Cloud — Vertex AI"]
        EMB["gemini-embedding-2"]
        LLM["gemini-3.6-flash"]
    end

    SENS -->|publish JSON| MQTT
    MQTT -->|subscribe| WRK
    WRK --> PG
    WRK --> RT
    EV --> RT
    USR --> CHAT & UPL
    DASH & CHAT & UPL --> RT
    RT --> DIAG --> GUARD
    GUARD -->|família documentada| RAGS
    GUARD -->|sem documento| RT
    DIAG <--> PG
    DIAG <--> ART
    RAGS <--> CH
    RAGS --> LLM
    UPL -->|ingestão| CH
    RAGS & UPL --> EMB
```

## 2. Decisões arquiteturais (ADRs)

| # | Decisão | Justificativa | Alternativa rejeitada |
|---|---------|---------------|----------------------|
| ADR-01 | **ChromaDB** (persistente, local) como vector store | Embarcado, filtros por metadados, roda na estação alvo (32 GB RAM / GPU 16 GB) | FAISS (sem metadados nativos), pgvector (acopla RAG ao Postgres) |
| ADR-02 | **`gemini-embedding-2`** (Vertex AI) para embeddings | Modelo mais recente do Google (GA abr/2026): multilíngue, contexto 8.192 tokens, dimensões Matryoshka (usamos 768), multimodal (evolução p/ diagramas dos manuais) | `gemini-embedding-001`, sentence-transformers local |
| ADR-03 | **`gemini-3.6-flash`** (Vertex AI) para geração, `temperature=0.2`, ID parametrizado via `.env` | Flash mais recente (jul/2026), contexto 1M, custo/latência baixos | Modelo local (qualidade PT-BR inferior no hardware alvo) |
| ADR-04 | Diagnóstico = **KNN + LightGBM** | KNN responde "ocorrências similares" literalmente; LightGBM dá o tipo de defeito com probabilidade; a concordância entre ambos vira métrica de confiança | Só KNN (sem probabilidade); rede neural (overkill p/ tabular) |
| ADR-05 | **Canonicalização de rótulos** via regras regex ordenadas em YAML | 151 rótulos brutos ruidosos (typos reais: `ddesbalanceado`, `cockecocked`, `mortor_desligado`) → 17 famílias; auditável, rótulo desconhecido = erro explícito | Clustering automático (não auditável) |
| ADR-06 | **FastAPI** como camada de serviço; Streamlit só consome a API | API reutilizável p/ integração industrial (SCADA/CMMS/historian) | Streamlit monolito |
| ADR-07 | **PostgreSQL** (Docker) simulando o banco corporativo | ETL real do CSV; agregações do dashboard em SQL | DuckDB/SQLite |
| ADR-08 | **docker-compose** (postgres + mosquitto + api + worker + ui) | Reprodutível na estação alvo | K8s (excesso p/ o escopo) |
| ADR-09 | Guardrail de cobertura documental **determinístico** — família documentada ⇔ possui chunks no Chroma | Requisito duro do edital; o LLM **nunca** decide se existe documento | Deixar o LLM decidir (risco de alucinação) |
| ADR-10 | Ingestão de eventos via **MQTT** (Mosquitto + worker paho-mqtt + simulador) | Protocolo de fato do chão de fábrica; ciclo completo sensor → diagnóstico → alerta de volta à planta | Só REST; OPC UA (setup pesado — citado como evolução) |
| ADR-11 | **Agente Google ADK apenas no /chat** (LlmAgent + 4 ferramentas + sessões no Postgres); o /diagnose permanece determinístico | Agente agrega valor onde há decisão dinâmica de ferramenta (perguntas exploratórias sobre histórico/documentos); no caminho crítico, um loop agêntico enfraqueceria as garantias anti-alucinação e a previsibilidade exigida pela prova | Agentificar todo o pipeline (imprevisível, caro no fluxo MQTT); function calling manual (perderia sessões/orquestração prontas do ADK) |

## 3. Fluxo do novo evento

```mermaid
sequenceDiagram
    participant U as Operador/Sistema
    participant A as FastAPI
    participant D as Diagnóstico (KNN+LGBM)
    participant P as PostgreSQL
    participant G as Guardrail
    participant R as RAG (ChromaDB)
    participant V as Vertex AI (Gemini)

    U->>A: POST /api/v1/diagnose (JSON do evento)
    A->>D: 18 features + 5 derivadas, normalizadas
    D->>D: LightGBM → família + probabilidade
    D->>D: KNN → 25 vizinhos históricos + concordância
    D->>P: estatísticas da família (qtde, período, freq/dia, timeline)
    D->>G: família de falha prevista
    alt família POSSUI documento (chunks no Chroma)
        G->>R: query = família + sintomas do evento
        R->>V: embedding da query (gemini-embedding-2)
        R-->>G: top-k chunks (doc, seção, página)
        G->>V: prompt de contexto fechado (gemini-3.6-flash)
        V-->>A: instruções de correção + citações determinísticas
    else família SEM documento
        G-->>A: "problema não documentado" + sugestão de registro (LLM não é chamado)
    end
    A-->>U: tipo de defeito, ocorrências, frequência, instruções (ou aviso)
```

## 4. Pipeline de dados e documentos

```mermaid
flowchart LR
    subgraph ETL["ETL de dados (scripts/ingest_data.py)"]
        CSV["banner.csv<br/>166.796 linhas"] --> CLEAN["Limpeza + dedup +<br/>validação"]
        CLEAN --> CANON["Canonicalização<br/>label_map.yaml<br/>151 rótulos → 17 famílias"]
        CANON --> PGL[("PostgreSQL<br/>events + fault_families<br/>+ label_map")]
        PGL --> FEAT["Treino (src/ml/train.py):<br/>StandardScaler + KNN + LightGBM"]
        FEAT --> ARTS["artifacts/<br/>joblib + metrics.json"]
    end
    subgraph DOCPIPE["Ingestão documental (scripts/ingest_docs.py)"]
        PDFS["Doc1..Doc6.pdf<br/>+ novos uploads via API"] --> PARSE["Extração pymupdf<br/>por seção"]
        PARSE --> CHUNK["Chunks ~1200 chars c/ overlap<br/>metadados: doc, seção, página, família"]
        CHUNK --> GEMB["gemini-embedding-2<br/>(dim 768)"]
        GEMB --> CHR[("ChromaDB<br/>collection: manuals")]
    end
```

**Cobertura documental inicial** (`src/rag/coverage.yaml`): rolamentos (4 famílias) → Doc1; desalinhamento → Doc2; desbalanceamento → Doc3; correia → Doc4; polia → Doc5; cocked_rotor → Doc6. Famílias **sem documento** — `eccentric_rotor`, `ventoinha`, `falta_fase` — acionam o fluxo "problema não documentado → sugerir registro" exigido pelo edital. Em runtime a cobertura é dinâmica: um documento cadastrado via `POST /documents` passa a cobrir a família imediatamente.

## 5. Modelo de dados (PostgreSQL)

```mermaid
erDiagram
    FAULT_FAMILIES ||--o{ LABEL_MAP : canonicaliza
    FAULT_FAMILIES ||--o{ EVENTS : classifica
    FAULT_FAMILIES ||--o{ DOC_COVERAGE : cobre
    DOCUMENTS ||--o{ DOC_COVERAGE : referencia
    EVENTS ||--o{ DIAGNOSES : gera

    FAULT_FAMILIES { int id PK  text name  bool is_fault }
    LABEL_MAP { text raw_label PK  int family_id FK }
    EVENTS { bigint id PK  timestamptz created_at  text raw_fault  int family_id FK  float features_18_colunas }
    DOCUMENTS { int id PK  text filename  text title  timestamptz ingested_at  text status }
    DOC_COVERAGE { int family_id FK  int document_id FK }
    DIAGNOSES { bigint id PK  bigint event_id  int predicted_family_id FK  float probability  jsonb neighbors  jsonb llm_response  timestamptz created_at }
```

## 6. Motor de diagnóstico (ML)

- **Features**: 18 colunas brutas (unidades SI; duplicatas in/s e °F descartadas por colinearidade) + 5 derivadas fisicamente motivadas (`src/ml/features.py`): ordem do pico (freq/freq_rotação — as assinaturas dos manuais são em 1x/2x RPM, não em Hz absoluto), razão radial/axial e razão HF/LF (defeitos de rolamento excitam alta frequência).
- **KNN** (25 vizinhos, distância euclidiana em espaço padronizado) indexa **todo** o histórico — responde diretamente ao requisito de "registros históricos com comportamento semelhante".
- **LightGBM** multiclasse (17 famílias, `class_weight=balanced`) fornece o tipo de defeito com probabilidade.
- **Confiança** = probabilidade × concordância dos vizinhos → `alta` / `media` / `baixa` (exibida na UI).

### Validação — honestidade metodológica

O dataset é composto por **sessões de coleta** (o rótulo bruto identifica a sessão). Leituras da mesma sessão são quase idênticas, o que invalida o split aleatório como métrica de generalização; e um split temporal puro é impossível (4 famílias só existem no período final). Reportamos as duas avaliações (`artifacts/metrics.json`):

| Avaliação | O que mede | Accuracy | F1 macro |
|---|---|---|---|
| **Holdout por sessão** (principal) | Generalizar p/ **novas campanhas** de coleta | ~0,14 | ~0,20 |
| Split aleatório estratificado (referência) | Separabilidade dos padrões dentro das sessões | ~0,89 | ~0,86 |

**Causa-raiz diagnosticada** (análise completa em [ANALYTICS.md](ANALYTICS.md) §4): o gap não é ruído de amostragem, é **confundimento entre sessão e rótulo**. Decompondo a variância de cada feature entre e dentro das campanhas de coleta, **16 das 18 features** dispersam mais entre campanhas do que dentro delas — a temperatura lidera com 8,8× (2,70 °C entre campanhas vs 0,31 °C dentro). Como cada condição foi coletada numa única campanha, o modelo pode acertar o rótulo pelo "carimbo" da campanha.

Um teste de ablação removendo a temperatura (registrado como `eval_session_holdout_no_temp`) **piorou** o F1 (0,195 → 0,165): o vazamento é sistêmico, não de uma feature — retirar a mais extrema só faz o modelo migrar para a próxima proxy. A correção é de coleta (múltiplas campanhas por condição), não de modelagem.

Mitigações no design atual: (1) o KNN compara contra o **histórico completo** e responde "ocorrências similares" em vez de afirmar uma classe — é a saída mais defensável sob esse teto e é literalmente o que o edital pede; (2) o gate de confiança (probabilidade × concordância dos vizinhos) sinaliza diagnóstico incerto em vez de afirmar com falsa segurança; (3) o dashboard expõe as três avaliações e a evidência do vazamento, em vez de anunciar os 89% do split aleatório.

## 7. Integração industrial — MQTT

```mermaid
sequenceDiagram
    participant S as Simulador de sensor<br/>(scripts/simulate_sensor.py)
    participant B as Mosquitto
    participant W as Ingestion Worker
    participant P as PostgreSQL
    participant A as FastAPI /diagnose
    participant D as Dashboard

    S->>B: publish sensors/maquina-01/telemetry (QoS 1)
    B->>W: message (subscribe sensors/+/telemetry)
    W->>W: valida (schema pydantic ÚNICO, o mesmo da API)
    alt payload válido
        W->>P: merge em events (idempotente por id)
        W->>A: POST /diagnose
        A-->>W: diagnóstico
        W->>B: publish sensors/maquina-01/alerts (se falha)
    else payload inválido
        W->>B: publish sensors/maquina-01/dlq (motivo + payload)
    end
    D->>P: leitura → eventos aparecem "ao vivo"
```

- **QoS 1** (*at-least-once*) + `session.merge` idempotente por `id` → nenhum evento perdido nem duplicado.
- **DLQ**: payload inválido nunca é descartado em silêncio.
- **Alertas**: o diagnóstico volta à planta por MQTT — é assim que um SCADA/CMMS consumiria a solução.
- Evolução p/ produção: TLS + autenticação no broker, OPC UA para CLPs, payloads Sparkplug B.

## 8. Anti-alucinação (critério explícito da prova)

1. **Guardrail determinístico**: o LLM só é chamado se a família tem chunks indexados no Chroma — cobertura nunca é decidida pelo modelo.
2. **Contexto fechado**: system prompt exige responder somente com base nos trechos fornecidos, com marcadores `[n]`.
3. **Citações determinísticas**: doc/seção/página vêm dos **metadados do retrieval**, não do texto do LLM — citação alucinada é estruturalmente impossível.
4. `temperature=0.2`.
5. **Confiança dupla** (LightGBM × KNN) exibida na UI; abaixo do limiar → "diagnóstico incerto".

## 9. Agente de chat — Google ADK

O `/chat` é atendido por um agente **Google ADK** (`LlmAgent` sobre `gemini-3.6-flash`) com memória de conversa persistida no Postgres (`DatabaseSessionService`, em **schema dedicado `adk`** — a tabela `events` do ADK colidiria com a do domínio) e 4 ferramentas que reutilizam os módulos existentes:

```mermaid
flowchart LR
    UI["Streamlit Chat<br/>(session_id por aba)"] --> EP["POST /api/v1/chat"]
    EP -->|ADK disponível| AG["LlmAgent (gemini-3.6-flash)<br/>Runner + sessões no Postgres"]
    EP -->|fallback automático| RAGQ["RAG de um passo<br/>(rag_service.chat)"]
    AG --> T1["consultar_historico<br/>(SQL no Postgres)"]
    AG --> T2["buscar_documentos<br/>(guardrail + ChromaDB)"]
    AG --> T3["diagnosticar_evento<br/>(DiagnosisEngine)"]
    AG --> T4["cobertura_documental"]
```

Propriedades importantes:
- **Guardrail dentro da ferramenta**: `buscar_documentos` verifica a cobertura documental ANTES do retrieval; família sem documento retorna `documentado=false` + aviso estruturado — o LLM transmite, nunca decide.
- **Citações determinísticas**: um coletor (`contextvars`) registra doc/seção/página dos chunks realmente recuperados; a UI exibe essas fontes, não texto gerado.
- **Fallback automático**: sem `google-adk`/credenciais GCP, `get_agent()` retorna `None` e o endpoint cai no RAG de um passo — o chat nunca fica indisponível (badge na UI indica o modo).
- **Fronteira arquitetural (ADR-11)**: o agente existe só onde há decisão dinâmica de ferramenta; `POST /diagnose` (usado pelo worker MQTT) permanece 100% determinístico.

## 10. Estrutura do repositório

```
FIESC/
├── ARCHITECTURE.md            # este documento
├── README.md                  # setup e uso
├── docker-compose.yml         # postgres + mosquitto + api + worker + ui
├── Dockerfile                 # imagem única (api/worker/ui via command)
├── .env.example
├── .github/workflows/ci.yml   # ruff → pytest (LLM mockado) → docker build
├── pyproject.toml
├── src/
│   ├── core/                  # config, schemas pydantic (únicos), modelos SQLAlchemy
│   ├── etl/                   # label_map.yaml + canonicalização auditável
│   ├── ml/                    # features físicas, treino, diagnóstico (KNN+LGBM)
│   ├── rag/                   # chunking, ChromaDB, guardrail, orquestração RAG
│   ├── llm/                   # cliente Vertex AI, prompts, agente ADK + ferramentas
│   ├── api/                   # FastAPI
│   ├── ingestion/             # worker MQTT (DLQ, alerts)
│   └── ui/                    # Streamlit
├── scripts/                   # ingest_data, ingest_docs, simulate_sensor
├── artifacts/                 # modelos treinados (gerados, fora do git)
└── tests/                     # 46 testes (unit + integração, 100% offline)
```

## 11. CI/CD (GitHub Actions)

```mermaid
flowchart LR
    PUSH["push / PR"] --> LINT["ruff check + format"]
    LINT --> TEST["pytest<br/>(Postgres service container,<br/>LLM/embeddings mockados)"]
    TEST --> BUILD["docker build"]
```

Credenciais (`GOOGLE_APPLICATION_CREDENTIALS`) só via GitHub Secrets / `.env` local. Os testes de CI **não** chamam o Vertex AI: o `FakeLLM` implementa a mesma interface do cliente real (embeddings determinísticos por hash + geração canned), injetado via `dependency_overrides` do FastAPI.

## 12. Restrição de hardware do edital

A estação alvo (32 GB RAM, GPU 16 GB) executa com folga: os artefatos ML somam < 100 MB (LightGBM + índice KNN), o ChromaDB local tem centenas de chunks, e os modelos pesados (LLM/embeddings) rodam como serviço gerenciado no Vertex AI — nenhuma inferência local de LLM é necessária.
