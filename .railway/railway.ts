/**
 * Infraestrutura da solução de Manutenção Prescritiva.
 *
 * Cinco serviços, um único repositório: api, ui e worker compartilham a mesma
 * imagem e se distinguem apenas pelo start command; mosquitto tem imagem
 * própria; postgres é gerenciado pelo Railway.
 *
 * Convenções seguidas aqui:
 * - Segredos nunca aparecem neste arquivo. As variáveis sensíveis são
 *   declaradas com preserve() e recebem valor via `railway variable set`.
 * - Comunicação entre serviços usa a rede privada, não os domínios públicos:
 *   evita egresso e reduz latência.
 * - O ChromaDB grava em volume persistente; sem ele, cada redeploy apagaria
 *   o índice e os documentos cadastrados pela interface.
 */
import {
  defineRailway,
  github,
  group,
  postgres,
  preserve,
  project,
  service,
  volume,
} from "railway/iac";

const REPO = "Lucas-Salomao/FIESC-processo-seletivo-02198-2026";
const BRANCH = "main";

// Só publica depois que o CI do GitHub (ruff + pytest + docker build) passar.
// `rootDirectory` fica no padrão (raiz do repositório) para api, ui e worker:
// os três compartilham a mesma imagem, construída pelo Dockerfile da raiz.
// Apontá-lo para a pasta do módulo faz o build sair sem dependência alguma.
const SOURCE = { branch: BRANCH, checkSuites: true } as const;

export default defineRailway(() => {
  const db = postgres("postgres");

  const mosquitto = service("mosquitto", {
    source: github(REPO, { ...SOURCE, rootDirectory: "mosquitto" }),
    env: {
      MQTT_USERNAME: preserve(),
      MQTT_PASSWORD: preserve(),
    },
  });

  const api = service("api", {
    source: github(REPO, SOURCE),
    // Envolvido em `sh -c` de propósito: o Railway executa o start command
    // diretamente (sem shell), então $PORT não seria expandido.
    start: 'sh -c "uvicorn src.api.main:app --host 0.0.0.0 --port ${PORT:-8000}"',
    env: {
      // PORT é declarado explicitamente para que ui e worker consigam
      // referenciá-lo: a porta injetada pelo Railway em runtime não é uma
      // variável referenciável entre serviços.
      PORT: "8000",
      DATABASE_URL: db.env.DATABASE_URL,

      // Vertex AI — gemini-3.6-flash e gemini-embedding-2 são servidos pelo
      // endpoint "global"; us-central1 devolve 404 para esses modelos.
      GOOGLE_CLOUD_PROJECT: "manutencao-prescritiva",
      GOOGLE_CLOUD_LOCATION: "global",
      GOOGLE_CREDENTIALS_B64: preserve(),
      GEMINI_CHAT_MODEL: "gemini-3.6-flash",
      GEMINI_EMBEDDING_MODEL: "gemini-embedding-2",
      EMBEDDING_DIM: "768",

      ARTIFACTS_DIR: "/app/artifacts",
      CHROMA_DIR: "/app/data/chroma",
      DOCS_DIR: "/app/documentos",
      // Indexa os PDFs no primeiro boot, quando o volume ainda está vazio.
      RAG_BOOTSTRAP: "true",

      MQTT_HOST: mosquitto.env.RAILWAY_PRIVATE_DOMAIN,
      MQTT_USERNAME: mosquitto.env.MQTT_USERNAME,
      MQTT_PASSWORD: mosquitto.env.MQTT_PASSWORD,
    },
    volumeMounts: {
      // A região é fixada explicitamente: omiti-la faria o plano zerar a
      // região do volume existente, o que é uma operação destrutiva.
      "/app/data/chroma": volume("chroma", {
        sizeMB: 1024,
        region: "us-east4-eqdc4a",
      }),
    },
  });

  // A referência entre serviços usa a sintaxe de interpolação do Railway em
  // string literal. Em template literal do TypeScript os objetos de referência
  // seriam convertidos para "[object Object]" antes de chegarem ao Railway.
  const ui = service("ui", {
    source: github(REPO, SOURCE),
    start:
      'sh -c "streamlit run src/ui/app.py --server.port ${PORT:-8501}' +
      ' --server.address 0.0.0.0 --server.headless true"',
    env: {
      API_URL: "http://${{api.RAILWAY_PRIVATE_DOMAIN}}:${{api.PORT}}",
    },
  });

  const worker = service("worker", {
    source: github(REPO, SOURCE),
    start: "python -m src.ingestion.mqtt_worker",
    env: {
      DATABASE_URL: db.env.DATABASE_URL,
      API_URL: "http://${{api.RAILWAY_PRIVATE_DOMAIN}}:${{api.PORT}}",
      MQTT_HOST: mosquitto.env.RAILWAY_PRIVATE_DOMAIN,
      MQTT_USERNAME: mosquitto.env.MQTT_USERNAME,
      MQTT_PASSWORD: mosquitto.env.MQTT_PASSWORD,
    },
  });

  return project("manutencao-prescritiva", {
    resources: [
      db,
      // Os grupos já registram os serviços que contêm.
      group("Aplicação", [api, ui]),
      group("Ingestão industrial", [worker, mosquitto]),
    ],
  });
});
