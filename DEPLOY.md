# Deploy — Railway

A infraestrutura é declarada em código em [`.railway/railway.ts`](.railway/railway.ts) e
aplicada com `railway config apply`. Não há passo manual no painel: o arquivo é a fonte de
verdade da topologia, e `railway config plan` mostra o diff antes de qualquer mudança.

## Topologia

| Serviço | Origem | Papel | Exposição |
|---|---|---|---|
| `postgres` | Plugin gerenciado | Histórico de eventos e sessões do agente | interna |
| `api` | Dockerfile (raiz) | FastAPI + modelo ML + RAG. Monta o volume `chroma` | HTTPS |
| `ui` | Dockerfile (raiz) | Streamlit; consome a API pela rede privada | HTTPS |
| `worker` | Dockerfile (raiz) | Ingestão MQTT → Postgres → diagnóstico → alertas | nenhuma |
| `mosquitto` | `mosquitto/Dockerfile` | Broker MQTT com autenticação | TCP proxy |

`api`, `ui` e `worker` compartilham a **mesma imagem**; o papel de cada um é definido pelo
start command. Isso mantém uma única superfície de build e garante que os três rodem
exatamente o mesmo código.

## Decisões de projeto

**Rede privada entre serviços.** `API_URL` aponta para
`http://${{api.RAILWAY_PRIVATE_DOMAIN}}:${{api.PORT}}` em vez do domínio público — evita
egresso, reduz latência e mantém o tráfego interno fora da internet.

**Volume para o índice vetorial.** O filesystem do container é efêmero. Sem o volume
montado em `/app/data/chroma`, cada redeploy apagaria os 268 chunks indexados e todo
documento cadastrado pela interface. Volumes do Railway pertencem a um único serviço — só
a `api` toca o ChromaDB, então a restrição não atrapalha.

**Bootstrap automático da base documental.** O volume nasce vazio e não há shell no
container para rodar `ingest_docs.py`. A API detecta o índice vazio no startup e indexa os
PDFs sozinha, em thread separada — são dezenas de chamadas ao Vertex AI (incluindo o OCR do
Doc1, que é escaneado) e bloquear o boot faria o healthcheck reprovar a instância.

**Broker com autenticação.** Um MQTT anônimo exposto na internet é varrido e abusado em
horas. O `mosquitto/entrypoint.sh` gera a configuração no boot a partir de
`MQTT_USERNAME`/`MQTT_PASSWORD`; sem credenciais ele registra um aviso em log e só então
cai em modo anônimo.

**Segredos fora do código.** `.railway/railway.ts` declara as variáveis sensíveis com
`preserve()` — o nome é versionado, o valor nunca. A service account do Vertex AI viaja em
base64 numa variável e é materializada em arquivo temporário com permissão `0600`
([`src/core/config.py`](src/core/config.py)).

## Provisionar do zero

```bash
npm install                                   # SDK de IaC do Railway
railway login
railway init --name manutencao-prescritiva
railway config plan                           # revisar o diff
railway config apply                          # criar os recursos
```

Segredos (nunca em linha de comando — `--stdin` evita o histórico do shell):

```bash
base64 -w0 service-account.json | railway variable set GOOGLE_CREDENTIALS_B64 --stdin --service api
printf '%s' 'fiesc'          | railway variable set MQTT_USERNAME --stdin --service mosquitto
printf '%s' '<senha-forte>'  | railway variable set MQTT_PASSWORD --stdin --service mosquitto
```

Exposição pública:

```bash
railway domain --service api
railway domain --service ui
railway tcp-proxy create --service mosquitto --port 1883
```

## Carga inicial dos dados

Os 166.796 eventos são carregados **da máquina local** apontando para o Postgres do
Railway — o CSV de 31 MB não precisa ir para a imagem nem para o repositório:

```bash
railway variable list --service postgres --json   # copiar DATABASE_PUBLIC_URL
DATABASE_URL='<url-publica>' python scripts/ingest_data.py --csv documentos/banner.csv
```

A base documental **não** exige passo manual: a API a indexa no primeiro boot. Para
reindexar ou acrescentar procedimentos depois, use a aba **Documentos** da interface ou
`POST /api/v1/documents`.

## Operação

```bash
railway logs --service api --lines 100        # logs de runtime
railway deployment list --service api         # histórico e status
railway redeploy --service api                # redeploy sem rebuild
railway variable list --service api           # variáveis efetivas
railway status                                # visão do projeto
```

Todo push na branch `main` dispara rebuild automático dos três serviços Python.

## Custo

Cinco serviços ativos 24/7 excedem o crédito incluso no plano Hobby. A `api` é a mais
pesada (carrega ~74 MB de artefatos e o índice KNN em memória, ~1 GB de RAM).

Para reduzir: `postgres` + `api` + `ui` já entregam diagnóstico, RAG, chat com agente e
todos os painéis. O par `worker` + `mosquitto` existe para demonstrar a integração
industrial e pode ser mantido apenas localmente, via `docker compose up`.

## Verificação pós-deploy

```bash
curl -s https://<api>/api/v1/health | jq          # artifacts_loaded, famílias documentadas
curl -s -X POST https://<api>/api/v1/diagnose \
     -H 'Content-Type: application/json' -d @evento.json | jq '.predicted_fault, .documented'
```

O `health` só reporta `documented_families` preenchido depois que o bootstrap termina
(alguns minutos no primeiro boot, por conta do OCR). Acompanhe por
`railway logs --service api | grep bootstrap`.
