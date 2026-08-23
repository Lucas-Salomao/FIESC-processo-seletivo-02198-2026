"""Simulador de sensor IIoT — replay do banner.csv via MQTT.

Publica eventos do dataset no tópico sensors/{machine}/telemetry, como um
gateway de aquisição real faria.

Uso:
    python scripts/simulate_sensor.py --rate 2 --machine maquina-01
    python scripts/simulate_sensor.py --family cocked_rotor --limit 10
    python scripts/simulate_sensor.py --invalid   # publica 1 payload inválido (demo da DLQ)
"""

import argparse
import json
import sys
import time
from pathlib import Path

import paho.mqtt.client as mqtt
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.core.config import get_settings  # noqa: E402
from src.etl.canonize import get_canonizer  # noqa: E402


def main() -> None:
    """Lê uma amostra do banner.csv e publica cada linha como evento MQTT,
    simulando um gateway de aquisição real (ou publica um payload inválido,
    com --invalid, para demonstrar o fluxo da dead-letter queue)."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", type=Path, default=Path("documentos/banner.csv"))
    parser.add_argument("--machine", default="maquina-01")
    parser.add_argument("--rate", type=float, default=2.0, help="segundos entre eventos")
    parser.add_argument("--limit", type=int, default=20, help="número de eventos")
    parser.add_argument("--family", default=None, help="filtra por família canônica")
    parser.add_argument("--invalid", action="store_true", help="publica payload inválido (DLQ)")
    args = parser.parse_args()

    settings = get_settings()
    client = mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2, client_id="fiesc-simulator"
    )
    if settings.mqtt_username:
        client.username_pw_set(settings.mqtt_username, settings.mqtt_password)
    client.connect(settings.mqtt_host, settings.mqtt_port)
    client.loop_start()
    topic = f"sensors/{args.machine}/telemetry"

    if args.invalid:
        client.publish(topic, json.dumps({"foo": "payload sem features"}), qos=1)
        print(f"Payload inválido publicado em {topic} (deve cair na DLQ).")
        time.sleep(1)
        client.loop_stop()
        return

    df = pd.read_csv(args.csv, parse_dates=["created_at"])
    if args.family:
        canonizer = get_canonizer()
        raw = df["fault"].astype(str).str.strip().str.lower()
        fam = raw.map(lambda x: canonizer.canonize(x).name)
        df = df[fam == args.family]
        if df.empty:
            sys.exit(f"Nenhum evento da família {args.family} no dataset.")

    sample = df.sample(n=min(args.limit, len(df)), random_state=None)
    print(f"Publicando {len(sample)} eventos em {topic} (1 a cada {args.rate}s)...")
    for _, row in sample.iterrows():
        payload = row.to_dict()
        payload["created_at"] = str(payload["created_at"])
        client.publish(topic, json.dumps(payload, ensure_ascii=False, default=str), qos=1)
        print(f"  -> id={payload['id']} fault={payload['fault']}")
        time.sleep(args.rate)

    time.sleep(1)
    client.loop_stop()
    print("Replay concluído.")


if __name__ == "__main__":
    main()
