import json
import os
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import paho.mqtt.client as mqtt
from flask import Flask, jsonify, request, send_from_directory

APP_DIR = Path(__file__).resolve().parent
WEB_DIR = APP_DIR / "web"
DATA_DIR = Path("/data")
OPTIONS_PATH = DATA_DIR / "options.json"
DATABASE_PATH = DATA_DIR / "acoustic_observatory.sqlite3"

DEFAULT_TOPIC = "atom_echo_noise/spectrum/state"
STALE_AFTER_SECONDS = 30
SUPERVISOR_MQTT_URL = "http://supervisor/services/mqtt"
MAX_HISTORY_ROWS = 720

app = Flask(__name__, static_folder=str(WEB_DIR), static_url_path="")
state_lock = threading.Lock()
database_lock = threading.Lock()

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
        with state_lock:
            state["error"] = f"Could not read options: {exc}"
        return {"mqtt_topic": DEFAULT_TOPIC}


def as_bool(value):
    return str(value).lower() in {"1", "true", "yes", "on"}


def environment_mqtt_settings():
    """Read the MQTT details injected by run.sh through Bashio."""
    host = os.getenv("MQTT_HOST")
    if not host:
        return None

    try:
        port = int(os.getenv("MQTT_PORT", "1883"))
    except ValueError:
        with state_lock:
            state["error"] = "Home Assistant MQTT service returned an invalid port"
        return None

    return {
        "host": host,
        "port": port,
        "username": os.getenv("MQTT_USERNAME"),
        "password": os.getenv("MQTT_PASSWORD"),
        "ssl": as_bool(os.getenv("MQTT_SSL", "false")),
        "source": "service",
    }


def supervisor_mqtt_settings():
    """Fallback for installations where service variables are unavailable."""
    token = os.getenv("SUPERVISOR_TOKEN")
    if not token:
        return None

    request_payload = urllib.request.Request(
        SUPERVISOR_MQTT_URL,
        headers={"Authorization": f"Bearer {token}"},
    )

    try:
        with urllib.request.urlopen(request_payload, timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        return None

    data = payload.get("data", payload)

    if not data.get("host"):
        return None

    try:
        port = int(data.get("port", 1883))
    except (TypeError, ValueError):
        return None

    return {
        "host": data.get("host"),
        "port": port,
        "username": data.get("username"),
        "password": data.get("password"),
        "ssl": as_bool(data.get("ssl", False)),
        "source": "supervisor",
    }


def mqtt_settings():
    service_settings = environment_mqtt_settings()
    if service_settings:
        return service_settings

    supervisor_settings = supervisor_mqtt_settings()
    if supervisor_settings:
        return supervisor_settings

    return {
        "host": os.getenv("MQTT_HOST", "core-mosquitto"),
        "port": int(os.getenv("MQTT_PORT", "1883")),
        "username": os.getenv("MQTT_USERNAME"),
        "password": os.getenv("MQTT_PASSWORD"),
        "ssl": as_bool(os.getenv("MQTT_SSL", "false")),
        "source": "fallback",
    }


def mqtt_error_message(prefix, result_code):
    with state_lock:
        source = state.get("mqtt_source", "unknown")

    if result_code == 5:
        if source == "service":
            return (
                f"{prefix}: authentication refused (code 5) using credentials "
                "provided by Home Assistant's MQTT service."
            )

        if source == "fallback":
            return (
                f"{prefix}: authentication refused (code 5). "
                "Home Assistant MQTT service settings were unavailable; "
                "using fallback credentials."
            )

        return (
            f"{prefix}: authentication refused (code 5) "
            f"using {source} MQTT credentials."
        )

    return f"{prefix}: {result_code}"


def init_database():
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    with database_lock:
        with sqlite3.connect(DATABASE_PATH) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS spectra (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    captured_at REAL NOT NULL,
                    dominant_frequency REAL,
                    dominant_magnitude REAL,
                    max_magnitude REAL NOT NULL DEFAULT 0,
                    low_band_magnitude REAL NOT NULL DEFAULT 0,
                    payload TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_spectra_captured_at
                ON spectra (captured_at)
                """
            )


def save_spectrum(captured_at, payload, summary):
    with database_lock:
        with sqlite3.connect(DATABASE_PATH) as connection:
            connection.execute(
                """
                INSERT INTO spectra (
                    captured_at,
                    dominant_frequency,
                    dominant_magnitude,
                    max_magnitude,
                    low_band_magnitude,
                    payload
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    captured_at,
                    summary["dominant_frequency"],
                    summary["dominant_magnitude"],
                    summary["max_magnitude"],
                    summary["low_band_magnitude"],
                    json.dumps(payload, separators=(",", ":")),
                ),
            )


def load_history(minutes):
    since = time.time() - minutes * 60

    with database_lock:
        with sqlite3.connect(DATABASE_PATH) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                """
                SELECT captured_at, dominant_frequency, dominant_magnitude,
                       max_magnitude, low_band_magnitude, payload
                FROM spectra
                WHERE captured_at >= ?
                ORDER BY captured_at DESC
                LIMIT ?
                """,
                (since, MAX_HISTORY_ROWS),
            ).fetchall()

    rows.reverse()
    items = []

    for row in rows:
        try:
            payload = json.loads(row["payload"])
        except json.JSONDecodeError:
            payload = {}

        items.append(
            {
                "captured_at": row["captured_at"],
                "dominant_frequency": row["dominant_frequency"],
                "dominant_magnitude": row["dominant_magnitude"],
                "max_magnitude": row["max_magnitude"],
                "low_band_magnitude": row["low_band_magnitude"],
                "points": parse_spectrum(payload),
            }
        )

    return items


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
        with state_lock:
            state["connected"] = True
            state["error"] = None
            topic = state["topic"]
        client.subscribe(topic)
    else:
        with state_lock:
            state["connected"] = False
            state["error"] = mqtt_error_message("MQTT connect failed", result_code)


def on_disconnect(client, userdata, result_code):
    with state_lock:
        state["connected"] = False

        if result_code != 0:
            state["error"] = mqtt_error_message("MQTT disconnected", result_code)


def on_message(client, userdata, message):
    try:
        payload = json.loads(message.payload.decode("utf-8"))
    except Exception as exc:
        with state_lock:
            state["error"] = f"Invalid spectrum payload: {exc}"
        return

    captured_at = time.time()
    summary = summarize_spectrum(payload)

    try:
        save_spectrum(captured_at, payload, summary)
    except Exception as exc:  # pragma: no cover - runtime diagnostics
        with state_lock:
            state["error"] = f"Could not store spectrum history: {exc}"
        return

    with state_lock:
        state["last_spectrum"] = payload
        state["last_message_at"] = captured_at
        state["message_count"] += 1
        state["error"] = None


def mqtt_worker():
    options = load_options()
    with state_lock:
        state["topic"] = options.get("mqtt_topic", DEFAULT_TOPIC)

    while True:
        settings = mqtt_settings()
        with state_lock:
            state["mqtt_source"] = settings.get("source", "fallback")

        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1)

        if settings["username"] and settings["password"]:
            client.username_pw_set(settings["username"], settings["password"])

        if settings["ssl"]:
            client.tls_set()

        client.on_connect = on_connect
        client.on_disconnect = on_disconnect
        client.on_message = on_message

        try:
            client.connect(settings["host"], settings["port"], keepalive=60)
            client.loop_forever()
        except Exception as exc:  # pragma: no cover - runtime diagnostics
            with state_lock:
                state["connected"] = False
                state["error"] = f"MQTT connection error: {exc}"
            time.sleep(5)


@app.get("/")
def index():
    return send_from_directory(WEB_DIR, "index.html")


@app.get("/api/state")
def api_state():
    now = time.time()

    with state_lock:
        snapshot = dict(state)

    last_message_at = snapshot["last_message_at"]
    age_seconds = None

    if last_message_at:
        age_seconds = max(0, int(now - last_message_at))

    summary = summarize_spectrum(snapshot["last_spectrum"])

    return jsonify(
        {
            "connected": snapshot["connected"],
            "topic": snapshot["topic"],
            "last_message_at": last_message_at,
            "message_count": snapshot["message_count"],
            "error": snapshot["error"],
            "age_seconds": age_seconds,
            "stale": age_seconds is None or age_seconds > STALE_AFTER_SECONDS,
            "mqtt_source": snapshot["mqtt_source"],
            "spectrum": summary,
        }
    )


@app.get("/api/history")
def api_history():
    try:
        minutes = int(request.args.get("minutes", "10"))
    except ValueError:
        minutes = 10

    minutes = max(1, min(minutes, 24 * 60))
    items = load_history(minutes)

    return jsonify(
        {
            "minutes": minutes,
            "count": len(items),
            "items": items,
        }
    )


if __name__ == "__main__":
    init_database()
    threading.Thread(target=mqtt_worker, daemon=True).start()
    app.run(host="0.0.0.0", port=8099)
