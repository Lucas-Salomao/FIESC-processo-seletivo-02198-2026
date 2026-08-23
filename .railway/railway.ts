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

export default defineRailway(() => {
  const db = postgres("postgres");

  const mosquitto = service("mosquitto", {
    source: github(REPO, { branch: BRANCH, rootDirectory: "mosquitto" }),
    env: {
      MQTT_USERNAME: preserve(),
      MQTT_PASSWORD: preserve(),
    },
  });

  const api = service("api", {
    source: github(REPO, { branch: BRANCH }),
    // Envolvido em `sh -c` de propósito: o Railway executa o start command
    // diretamente (sem shell), então $PORT não seria expandido.
    start: 'sh -c "uvicorn src.api.main:app --host 0.0.0.0 --port ${PORT:-8000}"',
    env: {
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

  const ui = service("ui", {
    source: github(REPO, { branch: BRANCH }),
    start:
      'sh -c "streamlit run src/ui/app.py --server.port ${PORT:-8501}' +
      ' --server.address 0.0.0.0 --server.headless true"',
    env: {
      API_URL: `http://${api.env.RAILWAY_PRIVATE_DOMAIN}:${api.env.PORT}`,
    },
  });

  const worker = service("worker", {
    source: github(REPO, { branch: BRANCH }),
    start: "python -m src.ingestion.mqtt_worker",
    env: {
      DATABASE_URL: db.env.DATABASE_URL,
      API_URL: `http://${api.env.RAILWAY_PRIVATE_DOMAIN}:${api.env.PORT}`,
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
