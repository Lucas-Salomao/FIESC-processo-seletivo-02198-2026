"""Worker de ingestão industrial via MQTT.

Assina `sensors/+/telemetry`, valida cada payload com o MESMO schema pydantic
da API (fonte única de validação), persiste no Postgres e dispara o
diagnóstico. Payload inválido vai para a dead-letter queue
(`sensors/{machine}/dlq`) com o motivo — nada é descartado em silêncio.
Diagnósticos de falha são republicados em `sensors/{machine}/alerts`,
fechando o ciclo sensor → diagnóstico → prescrição → planta.

Execução:  python -m src.ingestion.mqtt_worker
"""

import json
import logging
from datetime import UTC, datetime

import paho.mqtt.client as mqtt
import requests
from pydantic import ValidationError

from src.core.config import get_settings
from src.core.db import Event, FaultFamily, get_session_factory
from src.core.schemas import FEATURE_COLUMNS, SensorEvent
from src.etl.canonize import UnknownLabelError, get_canonizer

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("mqtt_worker")


class IngestionWorker:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.session_factory = get_session_factory()
        self.canonizer = get_canonizer()
        self.client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id="fiesc-ingestion-worker",
        )
        if self.settings.mqtt_username:
            # Broker exposto publicamente roda com autenticação; em
            # desenvolvimento as credenciais ficam vazias e o acesso é anônimo.
            self.client.username_pw_set(self.settings.mqtt_username, self.settings.mqtt_password)
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message

    # --- callbacks -----------------------------------------------------
    def _on_connect(self, client, userdata, flags, reason_code, properties) -> None:
        log.info(
            "Conectado ao broker (rc=%s); assinando %s",
            reason_code,
            self.settings.mqtt_telemetry_topic,
        )
        client.subscribe(self.settings.mqtt_telemetry_topic, qos=1)

    def _on_message(self, client, userdata, msg) -> None:
        machine_id = self._machine_from_topic(msg.topic)
        try:
            payload = json.loads(msg.payload)
            event = SensorEvent.model_validate(payload)
        except (json.JSONDecodeError, ValidationError) as exc:
            self._to_dlq(machine_id, msg.payload, str(exc))
            return

        self._persist(event)
        self._diagnose_and_alert(machine_id, event)

    # --- pipeline -------------------------------------------------------
    def _persist(self, event: SensorEvent) -> None:
        family_name = None
        if event.fault:
            try:
                family_name = self.canonizer.canonize(event.fault).name
            except UnknownLabelError:
                log.warning("Rótulo desconhecido no evento %s: %s", event.id, event.fault)

        with self.session_factory() as session:
            family_id = None
            if family_name:
                family_id = session.query(FaultFamily.id).filter_by(name=family_name).scalar()
            row = Event(
                id=event.id,
                created_at=event.created_at or datetime.now(UTC),
                raw_fault=event.fault,
                family_id=family_id,
                **{c: getattr(event, c) for c in FEATURE_COLUMNS},
            )
            session.merge(row)  # idempotente por id (QoS 1 = at-least-once)
            session.commit()
        log.info("Evento %s persistido.", event.id)

    def _diagnose_and_alert(self, machine_id: str, event: SensorEvent) -> None:
        try:
            response = requests.post(
                f"{self.settings.api_url}/api/v1/diagnose",
                json=json.loads(event.model_dump_json()),
                timeout=120,
            )
            response.raise_for_status()
            diagnosis = response.json()
        except requests.RequestException as exc:
            log.error("Falha ao diagnosticar evento %s: %s", event.id, exc)
            return

        if diagnosis.get("is_fault"):
            alert = {
                "event_id": event.id,
                "machine_id": machine_id,
                "fault": diagnosis["predicted_fault"],
                "probability": diagnosis["probability"],
                "confidence": diagnosis["confidence"],
                "documented": diagnosis["documented"],
                "ts": datetime.now(UTC).isoformat(),
            }
            self.client.publish(
                f"sensors/{machine_id}/alerts", json.dumps(alert, ensure_ascii=False), qos=1
            )
            log.info("ALERTA %s → %s", machine_id, diagnosis["predicted_fault"])

    def _to_dlq(self, machine_id: str, payload: bytes, reason: str) -> None:
        dlq = {
            "reason": reason[:2000],
            "payload": payload.decode("utf-8", errors="replace")[:5000],
            "ts": datetime.now(UTC).isoformat(),
        }
        self.client.publish(f"sensors/{machine_id}/dlq", json.dumps(dlq, ensure_ascii=False), qos=1)
        log.warning("Payload inválido de %s enviado à DLQ: %s", machine_id, reason[:200])

    # --- util -------------------------------------------------------------
    @staticmethod
    def _machine_from_topic(topic: str) -> str:
        parts = topic.split("/")
        return parts[1] if len(parts) >= 3 else "unknown"

    def run(self) -> None:
        self.client.connect(self.settings.mqtt_host, self.settings.mqtt_port, keepalive=60)
        log.info(
            "Worker de ingestão iniciado (broker %s:%s).",
            self.settings.mqtt_host,
            self.settings.mqtt_port,
        )
        self.client.loop_forever(retry_first_connection=True)


if __name__ == "__main__":
    IngestionWorker().run()
