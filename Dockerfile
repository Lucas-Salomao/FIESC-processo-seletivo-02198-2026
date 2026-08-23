# Imagem única compartilhada por API, worker e UI — o papel de cada serviço
# é definido pelo start command, não por imagens distintas.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# libgomp1 é exigido pelo LightGBM em runtime.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 curl \
    && rm -rf /var/lib/apt/lists/*

# Camada de dependências isolada da camada de código: instalamos primeiro com
# um pacote-esqueleto, para que alterar o código-fonte não reinstale ~1 GB de
# dependências a cada deploy.
COPY pyproject.toml README.md ./
RUN mkdir -p src && touch src/__init__.py && pip install .

# Código e recursos de runtime.
COPY src ./src
COPY scripts ./scripts
COPY images ./images
COPY documentos/*.pdf ./documentos/
COPY artifacts ./artifacts
RUN pip install --no-deps .

# Usuário sem privilégios. O diretório de dados é criado e cedido a ele aqui
# para que o ponto de montagem do volume já exista com o dono correto.
RUN useradd --create-home --uid 10001 app \
    && mkdir -p /app/data/chroma \
    && chown -R app:app /app
USER app

ENV ARTIFACTS_DIR=/app/artifacts \
    CHROMA_DIR=/app/data/chroma

EXPOSE 8000
