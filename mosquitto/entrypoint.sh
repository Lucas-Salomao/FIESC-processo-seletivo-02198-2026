#!/bin/sh
# Gera a configuração em tempo de execução: as credenciais chegam por variável
# de ambiente e nunca ficam na imagem nem no repositório.
set -eu

# A porta é fixa: o proxy TCP do orquestrador aponta para 1883, e os clientes
# internos usam a mesma. Railway não injeta PORT em serviços TCP.
PORT="${MQTT_LISTEN_PORT:-1883}"
CONF=/mosquitto/config/mosquitto.conf
PASSWD=/mosquitto/config/passwd

# Sem bind address explícito o Mosquitto escuta em todas as interfaces,
# incluindo IPv6 — exigido pela rede privada do Railway.
cat > "$CONF" <<CONF
listener ${PORT}
persistence false
log_dest stdout
log_type error
log_type warning
log_type notice
CONF

if [ -n "${MQTT_USERNAME:-}" ] && [ -n "${MQTT_PASSWORD:-}" ]; then
    # Remover antes torna a geração idempotente entre reinícios; o arquivo
    # precisa pertencer ao usuário `mosquitto`, que é quem roda o broker.
    rm -f "$PASSWD"
    mosquitto_passwd -c -b "$PASSWD" "$MQTT_USERNAME" "$MQTT_PASSWORD"
    chown mosquitto:mosquitto "$PASSWD"
    chmod 0600 "$PASSWD"
    printf 'allow_anonymous false\npassword_file %s\n' "$PASSWD" >> "$CONF"
    echo "mosquitto: autenticação habilitada para o usuário '${MQTT_USERNAME}'"
else
    # Só aceitável quando o broker não está exposto publicamente.
    printf 'allow_anonymous true\n' >> "$CONF"
    echo "mosquitto: AVISO — acesso anônimo (defina MQTT_USERNAME/MQTT_PASSWORD)"
fi

exec mosquitto -c "$CONF"
