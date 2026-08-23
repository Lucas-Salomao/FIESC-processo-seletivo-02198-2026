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

# Usuário sem privilégios. A troca de usuário acontece no entrypoint, após
# o ajuste de dono do volume montado — operação que exige root.
RUN useradd --create-home --uid 10001 app \
    && mkdir -p /app/data/chroma \
    && chown -R app:app /app
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

ENV ARTIFACTS_DIR=/app/artifacts \
    CHROMA_DIR=/app/data/chroma

EXPOSE 8000

# Inicia como root só para ceder o volume ao usuário `app`; o entrypoint
# baixa privilégios antes de executar o comando do serviço.
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
