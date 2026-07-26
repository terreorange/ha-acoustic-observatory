import json
import os
import threading
import time
from pathlib import Path

import paho.mqtt.client as mqtt
from flask import Flask, jsonify, send_from_directory

APP_DIR = Path(__file__).resolve().parent
WEB_DIR = APP_DIR / "web"
OPTIONS_PATH = Path("/data/options.json")

DEFAULT_TOPIC = "atom_echo_noise/spectrum/state"
STALE_AFTER_SECONDS = 30

app = Flask(__name__, static_folder=str(WEB_DIR), static_url_path="")

state = {
    "connected": False,
    "topic": DEFAULT_TOPIC,
    "last_message_at": None,
    "last_spectrum": None,
    "message_count": 0,
    "error": None,
}


def load_options():
    if not OPTIONS_PATH.exists():
        return {"mqtt_topic": DEFAULT_TOPIC}

    try:
        with OPTIONS_PATH.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except Exception as exc:  # pragma: no cover - startup diagnostics
        state["error"] = f"Could not read options: {exc}"
        return {"mqtt_topic": DEFAULT_TOPIC}


def mqtt_settings():
    return {
        "host": os.getenv("MQTT_HOST", "core-mosquitto"),
        "port": int(os.getenv("MQTT_PORT", "1883")),
        "username": os.getenv("MQTT_USERNAME"),
        "password": os.getenv("MQTT_PASSWORD"),
    }


def is_success_reason(reason_code):
    if reason_code == 0:
        return True

    value = getattr(reason_code, "value", None)
    if value == 0:
        return True

    is_failure = getattr(reason_code, "is_failure", None)
    if callable(is_failure):
        return not is_failure()
    if is_failure is not None:
        return not is_failure

    try:
        return int(reason_code) == 0
    except (TypeError, ValueError):
        return str(reason_code).lower() == "success"


def parse_spectrum(payload):
    bins = payload.get("bins", {})
    points = []

    for frequency, magnitude in bins.items():
        try:
            points.append(
                {
                    "frequency": float(frequency),
                    "magnitude": float(magnitude),
                }
            )
        except (TypeError, ValueError):
            continue

    points.sort(key=lambda point: point["frequency"])
    return points


def summarize_spectrum(payload):
    points = parse_spectrum(payload or {})

    if not points:
        return {
            "points": [],
            "dominant_frequency": None,
            "dominant_magnitude": None,
            "max_magnitude": 0.0,
            "low_band_magnitude": 0.0,
        }

    dominant = max(points, key=lambda point: point["magnitude"])
    low_points = [
        point for point in points
        if 40.0 <= point["frequency"] <= 250.0
    ]

    if low_points:
        low_band_magnitude = sum(
            point["magnitude"] for point in low_points
        ) / len(low_points)
    else:
        low_band_magnitude = 0.0

    return {
        "points": points,
        "dominant_frequency": dominant["frequency"],
        "dominant_magnitude": dominant["magnitude"],
        "max_magnitude": dominant["magnitude"],
        "low_band_magnitude": low_band_magnitude,
    }


def on_connect(client, userdata, flags, reason_code, properties=None):
    if is_success_reason(reason_code):
        state["connected"] = True
        state["error"] = None
        client.subscribe(state["topic"])
    else:
        state["connected"] = False
        state["error"] = f"MQTT connect failed: {reason_code}"


def on_disconnect(client, userdata, *args):
    state["connected"] = False

    reason = args[-2] if len(args) >= 2 else args[-1] if args else None
    if reason and not is_success_reason(reason):
        state["error"] = f"MQTT disconnected: {reason}"


def on_message(client, userdata, message):
    try:
        payload = json.loads(message.payload.decode("utf-8"))
    except Exception as exc:
        state["error"] = f"Invalid spectrum payload: {exc}"
        return

    state["last_spectrum"] = payload
    state["last_message_at"] = time.time()
    state["message_count"] += 1
    state["error"] = None


def mqtt_worker():
    options = load_options()
    state["topic"] = options.get("mqtt_topic", DEFAULT_TOPIC)

    settings = mqtt_settings()
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)

    if settings["username"] and settings["password"]:
        client.username_pw_set(settings["username"], settings["password"])

    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.on_message = on_message

    while True:
        try:
            client.connect(settings["host"], settings["port"], keepalive=60)
            client.loop_forever()
        except Exception as exc:  # pragma: no cover - runtime diagnostics
            state["connected"] = False
            state["error"] = f"MQTT connection error: {exc}"
            time.sleep(5)


@app.get("/")
def index():
    return send_from_directory(WEB_DIR, "index.html")


@app.get("/api/state")
def api_state():
    now = time.time()
    last_message_at = state["last_message_at"]
    age_seconds = None

    if last_message_at:
        age_seconds = max(0, int(now - last_message_at))

    summary = summarize_spectrum(state["last_spectrum"])

    return jsonify(
        {
            "connected": state["connected"],
            "topic": state["topic"],
            "last_message_at": last_message_at,
            "message_count": state["message_count"],
            "error": state["error"],
            "age_seconds": age_seconds,
            "stale": age_seconds is None or age_seconds > STALE_AFTER_SECONDS,
            "spectrum": summary,
        }
    )


if __name__ == "__main__":
    threading.Thread(target=mqtt_worker, daemon=True).start()
    app.run(host="0.0.0.0", port=8099)
