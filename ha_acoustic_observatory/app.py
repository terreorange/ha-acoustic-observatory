import json
import os
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import paho.mqtt.client as mqtt
from flask import Flask, jsonify, send_from_directory

APP_DIR = Path(__file__).resolve().parent
WEB_DIR = APP_DIR / "web"
OPTIONS_PATH = Path("/data/options.json")

DEFAULT_TOPIC = "atom_echo_noise/spectrum/state"
STALE_AFTER_SECONDS = 30
SUPERVISOR_MQTT_URL = "http://supervisor/services/mqtt"

app = Flask(__name__, static_folder=str(WEB_DIR), static_url_path="")

state = {
    "connected": False,
    "topic": DEFAULT_TOPIC,
    "last_message_at": None,
    "last_spectrum": None,
    "message_count": 0,
    "error": None,
    "mqtt_source": "fallback",
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


def supervisor_mqtt_settings():
    token = os.getenv("SUPERVISOR_TOKEN")
    if not token:
        state["error"] = "Supervisor token unavailable; MQTT service lookup disabled"
        return None

    request = urllib.request.Request(
        SUPERVISOR_MQTT_URL,
        headers={"Authorization": f"Bearer {token}"},
    )

    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        state["error"] = f"Could not read Supervisor MQTT service: {exc}"
        return None

    data = payload.get("data", payload)
    if not data.get("host"):
        state["error"] = "Supervisor MQTT service did not return a broker host"
        return None

    return {
        "host": data.get("host"),
        "port": int(data.get("port", 1883)),
        "username": data.get("username"),
        "password": data.get("password"),
        "source": "supervisor",
    }


def mqtt_settings():
    supervisor_settings = supervisor_mqtt_settings()
    if supervisor_settings:
        return supervisor_settings

    return {
        "host": os.getenv("MQTT_HOST", "core-mosquitto"),
        "port": int(os.getenv("MQTT_PORT", "1883")),
        "username": os.getenv("MQTT_USERNAME"),
        "password": os.getenv("MQTT_PASSWORD"),
        "source": "fallback",
    }


def mqtt_error_message(prefix, result_code):
    source = state.get("mqtt_source", "unknown")

    if result_code == 5:
        if source == "fallback":
            return (
                f"{prefix}: authentication refused (code 5). "
                "Supervisor MQTT settings were unavailable; using fallback credentials."
            )

        return (
            f"{prefix}: authentication refused (code 5) "
            f"using {source} MQTT credentials."
        )

    return f"{prefix}: {result_code}"


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


def on_connect(client, userdata, flags, result_code):
    if result_code == 0:
        state["connected"] = True
        state["error"] = None
        client.subscribe(state["topic"])
    else:
        state["connected"] = False
        state["error"] = mqtt_error_message("MQTT connect failed", result_code)


def on_disconnect(client, userdata, result_code):
    state["connected"] = False

    if result_code != 0:
        state["error"] = mqtt_error_message("MQTT disconnected", result_code)


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

    while True:
        settings = mqtt_settings()
        state["mqtt_source"] = settings.get("source", "fallback")

        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1)

        if settings["username"] and settings["password"]:
            client.username_pw_set(settings["username"], settings["password"])

        client.on_connect = on_connect
        client.on_disconnect = on_disconnect
        client.on_message = on_message

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
            "mqtt_source": state["mqtt_source"],
            "spectrum": summary,
        }
    )


if __name__ == "__main__":
    threading.Thread(target=mqtt_worker, daemon=True).start()
    app.run(host="0.0.0.0", port=8099)
