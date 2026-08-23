#!/bin/sh
# O volume persistente é montado pelo orquestrador com dono root. Ajustamos o
# dono aqui, ainda como root, e só então baixamos privilégios — de outro modo
# o ChromaDB não conseguiria criar o índice no ponto de montagem.
set -e

APP_UID=10001
CHROMA="${CHROMA_DIR:-/app/data/chroma}"

if [ "$(id -u)" = "0" ]; then
    mkdir -p "$CHROMA"
    chown -R "$APP_UID:$APP_UID" "$CHROMA"
    exec setpriv --reuid="$APP_UID" --regid="$APP_UID" --clear-groups "$@"
fi

exec "$@"
