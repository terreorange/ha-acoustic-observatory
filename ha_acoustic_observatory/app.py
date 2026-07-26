import csv
import io
import json
import math
import os
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import quote
from pathlib import Path

import paho.mqtt.client as mqtt
from flask import Flask, Response, jsonify, request, send_from_directory

APP_DIR = Path(__file__).resolve().parent
WEB_DIR = APP_DIR / "web"
DATA_DIR = Path("/data")
OPTIONS_PATH = DATA_DIR / "options.json"
DATABASE_PATH = DATA_DIR / "acoustic_observatory.sqlite3"

DEFAULT_TOPIC = "atom_echo_noise/spectrum/state"
STALE_AFTER_SECONDS = 30
SUPERVISOR_MQTT_URL = "http://supervisor/services/mqtt"
HOME_ASSISTANT_STATES_URL = "http://supervisor/core/api/states"
MAX_HISTORY_ROWS = 720
MAX_WEATHER_ROWS = 720
SIGNATURE_MIN_SAMPLES = 3
NUISANCE_KEYWORDS = ("nuisance", "entrepot", "entrepôt", "ronron", "groupe", "froid")
DEFAULT_WIND_SPEED_THRESHOLD_KMH = 15.0
DEFAULT_WIND_GUST_THRESHOLD_KMH = 30.0
DEFAULT_WEATHER_POLL_INTERVAL_SECONDS = 30

app = Flask(__name__, static_folder=str(WEB_DIR), static_url_path="")
state_lock = threading.Lock()
database_lock = threading.Lock()
options_lock = threading.Lock()

state = {
    "connected": False,
    "topic": DEFAULT_TOPIC,
    "last_message_at": None,
    "last_spectrum": None,
    "message_count": 0,
    "error": None,
    "mqtt_source": "fallback",
    "weather": {
        "configured": False,
        "windy": False,
        "speed_kmh": None,
        "gust_kmh": None,
        "last_update_at": None,
        "error": None,
    },
}


def default_options():
    return {
        "mqtt_topic": DEFAULT_TOPIC,
        "wind_speed_entity": "",
        "wind_gust_entity": "",
        "wind_speed_threshold_kmh": DEFAULT_WIND_SPEED_THRESHOLD_KMH,
        "wind_gust_threshold_kmh": DEFAULT_WIND_GUST_THRESHOLD_KMH,
        "weather_poll_interval_seconds": DEFAULT_WEATHER_POLL_INTERVAL_SECONDS,
    }


def load_options():
    options = default_options()

    if not OPTIONS_PATH.exists():
        return options

    try:
        with OPTIONS_PATH.open("r", encoding="utf-8") as handle:
            configured = json.load(handle)
    except Exception as exc:  # pragma: no cover - startup diagnostics
        with state_lock:
            state["error"] = f"Could not read options: {exc}"
        return options

    if isinstance(configured, dict):
        options.update(configured)

    return options


def current_options():
    with options_lock:
        return load_options()


def as_bool(value):
    return str(value).lower() in {"1", "true", "yes", "on"}


def as_float(value):
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return None


def speed_to_kmh(value, unit):
    numeric = as_float(value)

    if numeric is None:
        return None

    normalized_unit = str(unit or "km/h").strip().lower()

    if normalized_unit in {"km/h", "kmh", "kph", "kmph"}:
        return numeric

    if normalized_unit in {"m/s", "mps", "m·s⁻¹"}:
        return numeric * 3.6

    if normalized_unit in {"mph", "mi/h"}:
        return numeric * 1.609344

    if normalized_unit in {"kt", "kts", "kn", "knot", "knots", "noeud", "noeuds"}:
        return numeric * 1.852

    return numeric


def home_assistant_state(entity_id):
    token = os.getenv("SUPERVISOR_TOKEN")

    if not token:
        raise RuntimeError("Supervisor token unavailable")

    url = f"{HOME_ASSISTANT_STATES_URL}/{quote(entity_id, safe='')}"
    request_payload = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {token}"},
    )

    with urllib.request.urlopen(request_payload, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def read_speed_entity(entity_id):
    entity = str(entity_id or "").strip()

    if not entity:
        return None

    payload = home_assistant_state(entity)
    unit = payload.get("attributes", {}).get("unit_of_measurement")
    return speed_to_kmh(payload.get("state"), unit)


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


def mqtt_error_message(prefix, result_code, source=None):
    if source is None:
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
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    label TEXT NOT NULL,
                    notes TEXT NOT NULL DEFAULT '',
                    started_at REAL NOT NULL,
                    ended_at REAL
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_sessions_started_at
                ON sessions (started_at)
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS weather_samples (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    captured_at REAL NOT NULL,
                    wind_speed_kmh REAL,
                    wind_gust_kmh REAL,
                    windy INTEGER NOT NULL DEFAULT 0,
                    payload TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_weather_samples_captured_at
                ON weather_samples (captured_at)
                """
            )


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


def save_weather_sample(captured_at, sample):
    with database_lock:
        with sqlite3.connect(DATABASE_PATH) as connection:
            connection.execute(
                """
                INSERT INTO weather_samples (
                    captured_at,
                    wind_speed_kmh,
                    wind_gust_kmh,
                    windy,
                    payload
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    captured_at,
                    sample.get("speed_kmh"),
                    sample.get("gust_kmh"),
                    1 if sample.get("windy") else 0,
                    json.dumps(sample, separators=(",", ":")),
                ),
            )


def spectrum_vector(points):
    vector = {}

    for point in points:
        frequency = round(float(point["frequency"]), 1)
        vector[frequency] = max(0.0, float(point["magnitude"]))

    return vector


def normalize_vector(vector):
    norm = math.sqrt(sum(value * value for value in vector.values()))

    if norm <= 0:
        return {}

    return {
        frequency: value / norm
        for frequency, value in vector.items()
    }


def cosine_similarity(left, right):
    if not left or not right:
        return 0.0

    keys = set(left) & set(right)

    if not keys:
        return 0.0

    return sum(left[key] * right[key] for key in keys)


def average_vectors(vectors):
    totals = {}

    for vector in vectors:
        for frequency, value in vector.items():
            totals[frequency] = totals.get(frequency, 0.0) + value

    if not vectors:
        return {}

    return normalize_vector({
        frequency: total / len(vectors)
        for frequency, total in totals.items()
    })


def rows_to_points(rows):
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
    return rows_to_points(rows)


def load_weather_samples(minutes):
    since = time.time() - minutes * 60

    with database_lock:
        with sqlite3.connect(DATABASE_PATH) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                """
                SELECT captured_at, wind_speed_kmh, wind_gust_kmh, windy, payload
                FROM weather_samples
                WHERE captured_at >= ?
                ORDER BY captured_at ASC
                LIMIT ?
                """,
                (since, MAX_WEATHER_ROWS),
            ).fetchall()

    return [
        {
            "captured_at": row["captured_at"],
            "wind_speed_kmh": row["wind_speed_kmh"],
            "wind_gust_kmh": row["wind_gust_kmh"],
            "windy": bool(row["windy"]),
        }
        for row in rows
    ]


def weather_periods_from_samples(samples):
    periods = []
    current = None

    for sample in samples:
        if sample["windy"] and current is None:
            current = {
                "started_at": sample["captured_at"],
                "ended_at": sample["captured_at"],
                "max_speed_kmh": sample["wind_speed_kmh"],
                "max_gust_kmh": sample["wind_gust_kmh"],
            }
            continue

        if sample["windy"] and current:
            current["ended_at"] = sample["captured_at"]
            for key, sample_key in (
                ("max_speed_kmh", "wind_speed_kmh"),
                ("max_gust_kmh", "wind_gust_kmh"),
            ):
                value = sample.get(sample_key)
                if value is not None:
                    current[key] = max(current.get(key) or value, value)
            continue

        if not sample["windy"] and current:
            periods.append(current)
            current = None

    if current:
        periods.append(current)

    return periods


def load_session_rows(session_id):
    with database_lock:
        with sqlite3.connect(DATABASE_PATH) as connection:
            connection.row_factory = sqlite3.Row
            session = connection.execute(
                """
                SELECT id, label, notes, started_at, ended_at
                FROM sessions
                WHERE id = ?
                """,
                (session_id,),
            ).fetchone()

            if not session:
                return None, []

            end = session["ended_at"] or time.time()
            rows = connection.execute(
                """
                SELECT captured_at, dominant_frequency, dominant_magnitude,
                       max_magnitude, low_band_magnitude, payload
                FROM spectra
                WHERE captured_at >= ? AND captured_at <= ?
                ORDER BY captured_at ASC
                """,
                (session["started_at"], end),
            ).fetchall()

    return dict(session), rows


def build_session_summary(session, rows):
    items = rows_to_points(rows)
    vectors = [normalize_vector(spectrum_vector(item["points"])) for item in items]
    vectors = [vector for vector in vectors if vector]

    dominant_values = [
        item["dominant_frequency"] for item in items
        if item["dominant_frequency"] is not None
    ]
    low_values = [item["low_band_magnitude"] for item in items]

    duration = (session["ended_at"] or time.time()) - session["started_at"]

    return {
        "id": session["id"],
        "label": session["label"],
        "notes": session["notes"],
        "started_at": session["started_at"],
        "ended_at": session["ended_at"],
        "active": session["ended_at"] is None,
        "duration_seconds": max(0, int(duration)),
        "sample_count": len(items),
        "average_dominant_frequency": (
            sum(dominant_values) / len(dominant_values)
            if dominant_values else None
        ),
        "average_low_band_magnitude": (
            sum(low_values) / len(low_values)
            if low_values else 0.0
        ),
        "has_signature": len(vectors) >= SIGNATURE_MIN_SAMPLES,
        "signature": average_vectors(vectors),
    }


def list_sessions():
    with database_lock:
        with sqlite3.connect(DATABASE_PATH) as connection:
            connection.row_factory = sqlite3.Row
            sessions = connection.execute(
                """
                SELECT id, label, notes, started_at, ended_at
                FROM sessions
                ORDER BY started_at DESC
                LIMIT 30
                """
            ).fetchall()

    summaries = []

    for session in sessions:
        session_dict, rows = load_session_rows(session["id"])
        if session_dict:
            summary = build_session_summary(session_dict, rows)
            summary.pop("signature", None)
            summaries.append(summary)

    return summaries


def active_session():
    with database_lock:
        with sqlite3.connect(DATABASE_PATH) as connection:
            connection.row_factory = sqlite3.Row
            session = connection.execute(
                """
                SELECT id, label, notes, started_at, ended_at
                FROM sessions
                WHERE ended_at IS NULL
                ORDER BY started_at DESC
                LIMIT 1
                """
            ).fetchone()

    return dict(session) if session else None


def current_signature_match(summary):
    current_vector = normalize_vector(spectrum_vector(summary.get("points", [])))

    if not current_vector:
        return {
            "best_match": None,
            "nuisance_score": None,
            "nuisance_match": None,
        }

    completed = []

    with database_lock:
        with sqlite3.connect(DATABASE_PATH) as connection:
            connection.row_factory = sqlite3.Row
            sessions = connection.execute(
                """
                SELECT id, label, notes, started_at, ended_at
                FROM sessions
                WHERE ended_at IS NOT NULL
                ORDER BY started_at DESC
                LIMIT 50
                """
            ).fetchall()

    for session in sessions:
        session_dict, rows = load_session_rows(session["id"])
        if not session_dict:
            continue

        candidate = build_session_summary(session_dict, rows)
        signature = candidate.pop("signature", {})

        if not signature or candidate["sample_count"] < SIGNATURE_MIN_SAMPLES:
            continue

        score = round(100.0 * cosine_similarity(current_vector, signature), 1)
        candidate["similarity"] = max(0.0, min(100.0, score))
        completed.append(candidate)

    if not completed:
        return {
            "best_match": None,
            "nuisance_score": None,
            "nuisance_match": None,
        }

    best_match = max(completed, key=lambda item: item["similarity"])
    nuisance_candidates = [
        item for item in completed
        if any(keyword in item["label"].lower() for keyword in NUISANCE_KEYWORDS)
    ]
    nuisance_match = (
        max(nuisance_candidates, key=lambda item: item["similarity"])
        if nuisance_candidates else None
    )

    return {
        "best_match": best_match,
        "nuisance_score": nuisance_match["similarity"] if nuisance_match else None,
        "nuisance_match": nuisance_match,
    }


def on_connect(client, userdata, flags, result_code):
    if result_code == 0:
        with state_lock:
            state["connected"] = True
            state["error"] = None
            topic = state["topic"]
        client.subscribe(topic)
    else:
        error = mqtt_error_message("MQTT connect failed", result_code)
        with state_lock:
            state["connected"] = False
            state["error"] = error


def on_disconnect(client, userdata, result_code):
    error = None

    if result_code != 0:
        error = mqtt_error_message("MQTT disconnected", result_code)

    with state_lock:
        state["connected"] = False

        if error:
            state["error"] = error


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


def weather_worker():
    while True:
        options = current_options()
        speed_entity = str(options.get("wind_speed_entity") or "").strip()
        gust_entity = str(options.get("wind_gust_entity") or "").strip()
        configured = bool(speed_entity or gust_entity)

        speed_threshold = as_float(options.get("wind_speed_threshold_kmh"))
        gust_threshold = as_float(options.get("wind_gust_threshold_kmh"))
        interval = as_float(options.get("weather_poll_interval_seconds"))

        if speed_threshold is None:
            speed_threshold = DEFAULT_WIND_SPEED_THRESHOLD_KMH

        if gust_threshold is None:
            gust_threshold = DEFAULT_WIND_GUST_THRESHOLD_KMH

        if interval is None:
            interval = DEFAULT_WEATHER_POLL_INTERVAL_SECONDS

        interval = max(10, min(300, int(interval)))

        sample = {
            "configured": configured,
            "speed_entity": speed_entity,
            "gust_entity": gust_entity,
            "speed_threshold_kmh": speed_threshold,
            "gust_threshold_kmh": gust_threshold,
            "speed_kmh": None,
            "gust_kmh": None,
            "windy": False,
            "last_update_at": time.time(),
            "error": None,
        }

        if configured:
            try:
                sample["speed_kmh"] = read_speed_entity(speed_entity)
                sample["gust_kmh"] = read_speed_entity(gust_entity)
                sample["windy"] = (
                    (
                        sample["speed_kmh"] is not None
                        and sample["speed_kmh"] >= speed_threshold
                    )
                    or (
                        sample["gust_kmh"] is not None
                        and sample["gust_kmh"] >= gust_threshold
                    )
                )
                save_weather_sample(sample["last_update_at"], sample)
            except Exception as exc:  # pragma: no cover - runtime diagnostics
                sample["error"] = f"Could not read wind entities: {exc}"

        with state_lock:
            state["weather"] = sample

        time.sleep(interval)


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
    signature = current_signature_match(summary)

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
            "weather": snapshot["weather"],
            "active_session": active_session(),
            "signature": signature,
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
    weather_samples = load_weather_samples(minutes)

    return jsonify(
        {
            "minutes": minutes,
            "count": len(items),
            "items": items,
            "weather_samples": weather_samples,
            "wind_periods": weather_periods_from_samples(weather_samples),
        }
    )


@app.get("/api/sessions")
def api_sessions():
    return jsonify({"sessions": list_sessions()})


@app.post("/api/sessions/start")
def api_start_session():
    payload = request.get_json(silent=True) or {}
    label = str(payload.get("label", "")).strip()
    notes = str(payload.get("notes", "")).strip()

    if not label:
        return jsonify({"error": "Session label is required"}), 400

    with database_lock:
        with sqlite3.connect(DATABASE_PATH) as connection:
            connection.row_factory = sqlite3.Row
            existing = connection.execute(
                "SELECT id FROM sessions WHERE ended_at IS NULL LIMIT 1"
            ).fetchone()

            if existing:
                return jsonify({"error": "A session is already running"}), 409

            cursor = connection.execute(
                """
                INSERT INTO sessions (label, notes, started_at)
                VALUES (?, ?, ?)
                """,
                (label, notes, time.time()),
            )
            session_id = cursor.lastrowid

    session, rows = load_session_rows(session_id)
    return jsonify({"session": build_session_summary(session, rows)})


@app.post("/api/sessions/<int:session_id>/stop")
def api_stop_session(session_id):
    with database_lock:
        with sqlite3.connect(DATABASE_PATH) as connection:
            cursor = connection.execute(
                """
                UPDATE sessions
                SET ended_at = ?
                WHERE id = ? AND ended_at IS NULL
                """,
                (time.time(), session_id),
            )

            if cursor.rowcount == 0:
                return jsonify({"error": "Active session not found"}), 404

    session, rows = load_session_rows(session_id)
    return jsonify({"session": build_session_summary(session, rows)})


@app.get("/api/sessions/<int:session_id>/export.csv")
def api_export_session(session_id):
    session, rows = load_session_rows(session_id)

    if not session:
        return jsonify({"error": "Session not found"}), 404

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "captured_at",
        "dominant_frequency",
        "dominant_magnitude",
        "max_magnitude",
        "low_band_magnitude",
        "frequency",
        "magnitude",
    ])

    for item in rows_to_points(rows):
        for point in item["points"]:
            writer.writerow([
                item["captured_at"],
                item["dominant_frequency"],
                item["dominant_magnitude"],
                item["max_magnitude"],
                item["low_band_magnitude"],
                point["frequency"],
                point["magnitude"],
            ])

    safe_label = "".join(
        char if char.isalnum() or char in {"-", "_"} else "_"
        for char in session["label"].lower()
    ).strip("_") or "session"

    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={
            "Content-Disposition": (
                f"attachment; filename=acoustic_{session_id}_{safe_label}.csv"
            )
        },
    )


if __name__ == "__main__":
    init_database()
    threading.Thread(target=mqtt_worker, daemon=True).start()
    threading.Thread(target=weather_worker, daemon=True).start()
    app.run(host="0.0.0.0", port=8099)
