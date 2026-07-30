import csv
import io
import json
import math
import os
import sqlite3
import tempfile
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
CONFIG_DIR = Path("/config")
OPTIONS_PATH = DATA_DIR / "options.json"
DATABASE_NAME = "acoustic_observatory.sqlite3"
LEGACY_DATABASE_PATH = DATA_DIR / DATABASE_NAME
# Resolved at startup by setup_storage(), which prefers the mapped config
# directory because /data does not survive an uninstall.
DATABASE_PATH = LEGACY_DATABASE_PATH

DEFAULT_TOPIC = "atom_echo_noise/spectrum/state"
STALE_AFTER_SECONDS = 30
SUPERVISOR_MQTT_URL = "http://supervisor/services/mqtt"
HOME_ASSISTANT_STATES_URL = "http://supervisor/core/api/states"
MAX_HISTORY_ROWS = 2016
MAX_WEATHER_ROWS = 2016
MAX_HISTORY_MINUTES = 14 * 24 * 60
SIGNATURE_MIN_SAMPLES = 3
WINDOW_MAX_SAMPLES = 3000
WINDOW_SERIES_BUCKETS = 120
WINDOW_MAX_SPAN_SECONDS = 31 * 24 * 60 * 60
DEFAULT_BAND_MIN_HZ = 20.0
DEFAULT_BAND_MAX_HZ = 250.0
BAND_CHANGE_LIMIT = 5
DOMINANT_HISTOGRAM_LIMIT = 6
CONSTANT_NOISE_TOLERANCE_DB = 3.0
NUISANCE_KEYWORDS = ("nuisance", "entrepot", "entrepôt", "ronron", "groupe", "froid")
DEFAULT_WIND_SPEED_THRESHOLD_KMH = 15.0
DEFAULT_WIND_GUST_THRESHOLD_KMH = 30.0
DEFAULT_WEATHER_POLL_INTERVAL_SECONDS = 30
DEFAULT_SPECTRUM_RETENTION_DAYS = 14
DEFAULT_WEATHER_RETENTION_DAYS = 30
DEFAULT_DATABASE_CLEANUP_INTERVAL_HOURS = 6
DEFAULT_CORRELATION_WINDOW_HOURS = 48
DEFAULT_CORRELATION_BUCKET_MINUTES = 10
MIN_CORRELATION_POINTS = 8
MAX_CORRELATION_BUCKETS = 2000
PUBLISH_INTERVAL_SECONDS = 60
DISCOVERY_PREFIX = "homeassistant"
PUBLISH_PREFIX = "ha_acoustic_observatory"
PUBLISHED_ENTITIES = (
    {
        "key": "low_band_level",
        "name": "Niveau bande basse",
        "unit": "idx",
        "icon": "mdi:waveform",
        "state_class": "measurement",
    },
    {
        "key": "temperature_sensitivity",
        "name": "Sensibilite thermique",
        "unit": "idx/°C",
        "icon": "mdi:thermometer-lines",
        "state_class": "measurement",
    },
    {
        "key": "temperature_correlation",
        "name": "Correlation chaleur-bruit",
        "unit": "%",
        "icon": "mdi:chart-scatter-plot",
        "state_class": "measurement",
    },
)

app = Flask(__name__, static_folder=str(WEB_DIR), static_url_path="")
state_lock = threading.Lock()
database_lock = threading.Lock()
options_lock = threading.Lock()
publish_lock = threading.Lock()
publish_client = None

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
        "temperature_c": None,
        "last_update_at": None,
        "error": None,
    },
    "storage": {
        "last_cleanup_at": None,
        "next_cleanup_at": None,
        "spectrum_retention_days": DEFAULT_SPECTRUM_RETENTION_DAYS,
        "weather_retention_days": DEFAULT_WEATHER_RETENTION_DAYS,
        "deleted_spectra": 0,
        "deleted_weather_samples": 0,
        "database_path": str(LEGACY_DATABASE_PATH),
        "database_persistent": False,
        "migrated_spectra": None,
        "migration_error": None,
        "error": None,
    },
}


def default_options():
    return {
        "mqtt_topic": DEFAULT_TOPIC,
        "wind_speed_entity": "",
        "wind_gust_entity": "",
        "outdoor_temperature_entity": "",
        "wind_speed_threshold_kmh": DEFAULT_WIND_SPEED_THRESHOLD_KMH,
        "wind_gust_threshold_kmh": DEFAULT_WIND_GUST_THRESHOLD_KMH,
        "weather_poll_interval_seconds": DEFAULT_WEATHER_POLL_INTERVAL_SECONDS,
        "correlation_window_hours": DEFAULT_CORRELATION_WINDOW_HOURS,
        "correlation_bucket_minutes": DEFAULT_CORRELATION_BUCKET_MINUTES,
        "publish_ha_entities": True,
        "spectrum_retention_days": DEFAULT_SPECTRUM_RETENTION_DAYS,
        "weather_retention_days": DEFAULT_WEATHER_RETENTION_DAYS,
        "database_cleanup_interval_hours": DEFAULT_DATABASE_CLEANUP_INTERVAL_HOURS,
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


def as_int(value):
    try:
        return int(float(str(value).replace(",", ".")))
    except (TypeError, ValueError):
        return None


def bounded_int(value, default, minimum, maximum):
    numeric = as_int(value)

    if numeric is None:
        numeric = default

    return max(minimum, min(maximum, numeric))


def bounded_float(value, default, minimum, maximum):
    numeric = as_float(value)

    if numeric is None:
        numeric = default

    return max(minimum, min(maximum, numeric))


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


def temperature_to_celsius(value, unit):
    numeric = as_float(value)

    if numeric is None:
        return None

    normalized_unit = str(unit or "°C").strip().lower()

    if normalized_unit in {"°f", "f", "fahrenheit"}:
        return (numeric - 32.0) / 1.8

    if normalized_unit in {"k", "°k", "kelvin"}:
        return numeric - 273.15

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


def read_temperature_entity(entity_id):
    entity = str(entity_id or "").strip()

    if not entity:
        return None

    payload = home_assistant_state(entity)
    unit = payload.get("attributes", {}).get("unit_of_measurement")
    return temperature_to_celsius(payload.get("state"), unit)


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


def ensure_column(connection, table, column, definition):
    """Add a column to an existing database created by an older add-on version."""
    existing = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}

    if column not in existing:
        connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def resolve_database_path():
    """Pick where the database lives, preferring the mapped add-on config directory.

    Home Assistant destroys /data when an add-on is uninstalled, so a database
    kept there is lost on reinstall even when the uninstall dialog offers to keep
    the add-on data. The directory mapped through `addon_config` survives, and is
    reachable from File Editor or Samba for a manual copy.
    """
    if CONFIG_DIR.is_dir() and os.access(CONFIG_DIR, os.W_OK):
        return CONFIG_DIR / DATABASE_NAME

    return LEGACY_DATABASE_PATH


def spectra_row_count(path):
    with sqlite3.connect(path) as connection:
        return connection.execute("SELECT COUNT(*) FROM spectra").fetchone()[0]


def migrate_legacy_database(target):
    """Move a database left in /data by an earlier version to its durable home.

    Returns (migrated_rows, error). The source is removed only once the copy has
    been verified to hold the same number of spectra.
    """
    if target == LEGACY_DATABASE_PATH or target.exists():
        return None, None

    if not LEGACY_DATABASE_PATH.exists():
        return None, None

    try:
        with sqlite3.connect(LEGACY_DATABASE_PATH) as source:
            with sqlite3.connect(target) as destination:
                source.backup(destination)

        copied = spectra_row_count(target)
        original = spectra_row_count(LEGACY_DATABASE_PATH)

        if copied != original:
            raise sqlite3.DatabaseError(f"copied {copied} of {original} spectra")
    except (sqlite3.Error, OSError) as exc:
        try:
            target.unlink(missing_ok=True)
        except OSError:
            pass

        return None, f"Could not move the database out of /data: {exc}"

    LEGACY_DATABASE_PATH.unlink(missing_ok=True)
    return copied, None


def setup_storage():
    global DATABASE_PATH

    DATABASE_PATH = resolve_database_path()
    migrated, error = migrate_legacy_database(DATABASE_PATH)

    with state_lock:
        state["storage"]["database_path"] = str(DATABASE_PATH)
        state["storage"]["database_persistent"] = DATABASE_PATH != LEGACY_DATABASE_PATH
        state["storage"]["migrated_spectra"] = migrated
        # Kept separate from "error", which the recurring cleanup resets.
        state["storage"]["migration_error"] = error

    init_database()


def init_database():
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)

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
                    outdoor_temperature_c REAL,
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
            ensure_column(
                connection, "weather_samples", "outdoor_temperature_c", "REAL"
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
                    outdoor_temperature_c,
                    windy,
                    payload
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    captured_at,
                    sample.get("speed_kmh"),
                    sample.get("gust_kmh"),
                    sample.get("temperature_c"),
                    1 if sample.get("windy") else 0,
                    json.dumps(sample, separators=(",", ":")),
                ),
            )


def cleanup_database():
    options = current_options()
    now = time.time()

    spectrum_retention_days = bounded_int(
        options.get("spectrum_retention_days"),
        DEFAULT_SPECTRUM_RETENTION_DAYS,
        1,
        365,
    )
    weather_retention_days = bounded_int(
        options.get("weather_retention_days"),
        DEFAULT_WEATHER_RETENTION_DAYS,
        1,
        365,
    )

    spectrum_cutoff = now - spectrum_retention_days * 24 * 60 * 60
    weather_cutoff = now - weather_retention_days * 24 * 60 * 60
    deleted_spectra = 0
    deleted_weather_samples = 0

    with database_lock:
        with sqlite3.connect(DATABASE_PATH) as connection:
            cursor = connection.execute(
                "DELETE FROM spectra WHERE captured_at < ?",
                (spectrum_cutoff,),
            )
            deleted_spectra = cursor.rowcount

            cursor = connection.execute(
                "DELETE FROM weather_samples WHERE captured_at < ?",
                (weather_cutoff,),
            )
            deleted_weather_samples = cursor.rowcount

        if deleted_spectra > 0 or deleted_weather_samples > 0:
            with sqlite3.connect(DATABASE_PATH) as connection:
                connection.execute("VACUUM")

    interval_hours = bounded_int(
        options.get("database_cleanup_interval_hours"),
        DEFAULT_DATABASE_CLEANUP_INTERVAL_HOURS,
        1,
        168,
    )

    with state_lock:
        previous = dict(state["storage"])

    cleanup_state = {
        **previous,
        "last_cleanup_at": now,
        "next_cleanup_at": now + interval_hours * 60 * 60,
        "spectrum_retention_days": spectrum_retention_days,
        "weather_retention_days": weather_retention_days,
        "deleted_spectra": max(0, deleted_spectra),
        "deleted_weather_samples": max(0, deleted_weather_samples),
        "error": None,
    }

    with state_lock:
        state["storage"] = cleanup_state

    return cleanup_state


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


def sampled_spectrum_rows(start, end, max_samples):
    """Read a time window, striding over ids so a long window stays representative.

    A plain LIMIT would keep only the newest rows and silently drop the start of
    the window, which is exactly what matters when analysing a past event.
    """
    with database_lock:
        with sqlite3.connect(DATABASE_PATH) as connection:
            connection.row_factory = sqlite3.Row
            total = connection.execute(
                """
                SELECT COUNT(*) FROM spectra
                WHERE captured_at >= ? AND captured_at <= ?
                """,
                (start, end),
            ).fetchone()[0]

            stride = max(1, math.ceil(total / max_samples)) if total else 1
            rows = connection.execute(
                """
                SELECT captured_at, dominant_frequency, dominant_magnitude,
                       max_magnitude, low_band_magnitude, payload
                FROM spectra
                WHERE captured_at >= ? AND captured_at <= ? AND (id % ?) = 0
                ORDER BY captured_at ASC
                """,
                (start, end, stride),
            ).fetchall()

    return rows, total, stride


def load_history(minutes):
    now = time.time()
    rows, _total, _stride = sampled_spectrum_rows(
        now - minutes * 60, now, MAX_HISTORY_ROWS
    )
    return rows_to_points(rows)


def load_weather_samples(minutes):
    since = time.time() - minutes * 60

    with database_lock:
        with sqlite3.connect(DATABASE_PATH) as connection:
            connection.row_factory = sqlite3.Row
            total = connection.execute(
                """
                SELECT COUNT(*) FROM weather_samples
                WHERE captured_at >= ?
                """,
                (since,),
            ).fetchone()[0]
            stride = max(1, math.ceil(total / MAX_WEATHER_ROWS)) if total else 1
            rows = connection.execute(
                """
                SELECT captured_at, wind_speed_kmh, wind_gust_kmh,
                       outdoor_temperature_c, windy, payload
                FROM weather_samples
                WHERE captured_at >= ? AND (id % ?) = 0
                ORDER BY captured_at ASC
                """,
                (since, stride),
            ).fetchall()

    return [
        {
            "captured_at": row["captured_at"],
            "wind_speed_kmh": row["wind_speed_kmh"],
            "wind_gust_kmh": row["wind_gust_kmh"],
            "temperature_c": row["outdoor_temperature_c"],
            "windy": bool(row["windy"]),
        }
        for row in rows
    ]


def linear_regression(pairs):
    """Least-squares fit of level against temperature, with Pearson correlation."""
    count = len(pairs)

    if count < MIN_CORRELATION_POINTS:
        return None

    mean_temperature = sum(pair[0] for pair in pairs) / count
    mean_level = sum(pair[1] for pair in pairs) / count

    temperature_variance = sum(
        (pair[0] - mean_temperature) ** 2 for pair in pairs
    )
    level_variance = sum((pair[1] - mean_level) ** 2 for pair in pairs)
    covariance = sum(
        (pair[0] - mean_temperature) * (pair[1] - mean_level) for pair in pairs
    )

    if temperature_variance <= 0 or level_variance <= 0:
        return None

    slope = covariance / temperature_variance
    correlation = covariance / math.sqrt(temperature_variance * level_variance)

    return {
        "slope": slope,
        "intercept": mean_level - slope * mean_temperature,
        "correlation": correlation,
        "r_squared": correlation * correlation,
        "sample_count": count,
        "mean_temperature_c": mean_temperature,
        "mean_level": mean_level,
        "min_temperature_c": min(pair[0] for pair in pairs),
        "max_temperature_c": max(pair[0] for pair in pairs),
    }


def correlation_buckets(hours, bucket_minutes, exclude_windy):
    """Average acoustic level and temperature over the same time buckets."""
    since = time.time() - hours * 60 * 60
    bucket_seconds = max(60, bucket_minutes * 60)

    with database_lock:
        with sqlite3.connect(DATABASE_PATH) as connection:
            connection.row_factory = sqlite3.Row
            spectrum_rows = connection.execute(
                """
                SELECT CAST(captured_at / ? AS INTEGER) AS bucket,
                       AVG(low_band_magnitude) AS level,
                       AVG(dominant_frequency) AS dominant_frequency,
                       COUNT(*) AS sample_count
                FROM spectra
                WHERE captured_at >= ?
                GROUP BY bucket
                ORDER BY bucket ASC
                LIMIT ?
                """,
                (bucket_seconds, since, MAX_CORRELATION_BUCKETS),
            ).fetchall()

            weather_rows = connection.execute(
                """
                SELECT CAST(captured_at / ? AS INTEGER) AS bucket,
                       AVG(outdoor_temperature_c) AS temperature_c,
                       MAX(windy) AS windy
                FROM weather_samples
                WHERE captured_at >= ? AND outdoor_temperature_c IS NOT NULL
                GROUP BY bucket
                ORDER BY bucket ASC
                LIMIT ?
                """,
                (bucket_seconds, since, MAX_CORRELATION_BUCKETS),
            ).fetchall()

    weather_by_bucket = {row["bucket"]: row for row in weather_rows}
    points = []

    for row in spectrum_rows:
        weather = weather_by_bucket.get(row["bucket"])

        if not weather or weather["temperature_c"] is None:
            continue

        windy = bool(weather["windy"])

        if exclude_windy and windy:
            continue

        points.append(
            {
                "captured_at": row["bucket"] * bucket_seconds,
                "temperature_c": weather["temperature_c"],
                "level": row["level"],
                "dominant_frequency": row["dominant_frequency"],
                "sample_count": row["sample_count"],
                "windy": windy,
            }
        )

    return points


def build_correlation(hours, bucket_minutes, exclude_windy):
    points = correlation_buckets(hours, bucket_minutes, exclude_windy)
    regression = linear_regression(
        [(point["temperature_c"], point["level"]) for point in points]
    )

    return {
        "hours": hours,
        "bucket_minutes": bucket_minutes,
        "exclude_windy": exclude_windy,
        "count": len(points),
        "min_points": MIN_CORRELATION_POINTS,
        "points": points,
        "regression": regression,
    }


def correlation_settings(options):
    hours = bounded_int(
        options.get("correlation_window_hours"),
        DEFAULT_CORRELATION_WINDOW_HOURS,
        1,
        720,
    )
    bucket_minutes = bounded_int(
        options.get("correlation_bucket_minutes"),
        DEFAULT_CORRELATION_BUCKET_MINUTES,
        1,
        120,
    )

    return hours, bucket_minutes


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


def band_points(points, min_hz, max_hz):
    return [
        point for point in points
        if min_hz <= point["frequency"] <= max_hz
    ]


def band_level(points, min_hz, max_hz):
    """Mean magnitude of the bins that fall inside the analysed frequency band."""
    selected = band_points(points, min_hz, max_hz)

    if not selected:
        return None

    return sum(point["magnitude"] for point in selected) / len(selected)


def band_dominant_frequency(points, min_hz, max_hz):
    selected = band_points(points, min_hz, max_hz)

    if not selected:
        return None

    return max(selected, key=lambda point: point["magnitude"])["frequency"]


def level_statistics(values):
    if not values:
        return None

    ordered = sorted(values)
    count = len(ordered)

    def percentile(ratio):
        position = ratio * (count - 1)
        lower = math.floor(position)
        upper = math.ceil(position)

        if lower == upper:
            return ordered[lower]

        return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)

    return {
        "mean": sum(ordered) / count,
        "median": percentile(0.5),
        "p90": percentile(0.9),
        "min": ordered[0],
        "max": ordered[-1],
    }


def average_band_spectrum(items, min_hz, max_hz):
    """Average magnitude per frequency bin over every sample of the window."""
    totals = {}
    counts = {}

    for item in items:
        for point in band_points(item["points"], min_hz, max_hz):
            frequency = round(point["frequency"], 1)
            totals[frequency] = totals.get(frequency, 0.0) + point["magnitude"]
            counts[frequency] = counts.get(frequency, 0) + 1

    return [
        {"frequency": frequency, "magnitude": totals[frequency] / counts[frequency]}
        for frequency in sorted(totals)
    ]


def band_level_series(items, min_hz, max_hz, buckets=WINDOW_SERIES_BUCKETS):
    if not items:
        return []

    start = items[0]["captured_at"]
    end = items[-1]["captured_at"]
    width = max(1e-6, (end - start) / max(1, buckets))
    grouped = {}

    for item in items:
        level = band_level(item["points"], min_hz, max_hz)

        if level is None:
            continue

        index = min(buckets - 1, int((item["captured_at"] - start) / width))
        bucket = grouped.setdefault(index, {"total": 0.0, "count": 0})
        bucket["total"] += level
        bucket["count"] += 1

    return [
        {
            "captured_at": start + (index + 0.5) * width,
            "level": grouped[index]["total"] / grouped[index]["count"],
        }
        for index in sorted(grouped)
    ]


def dominant_histogram(items, min_hz, max_hz, limit=DOMINANT_HISTOGRAM_LIMIT):
    """How often each frequency carried the in-band peak during the window."""
    counts = {}

    for item in items:
        frequency = band_dominant_frequency(item["points"], min_hz, max_hz)

        if frequency is None:
            continue

        rounded = round(frequency, 1)
        counts[rounded] = counts.get(rounded, 0) + 1

    total = sum(counts.values())

    if not total:
        return []

    ordered = sorted(counts.items(), key=lambda entry: entry[1], reverse=True)

    return [
        {
            "frequency": frequency,
            "count": count,
            "share_percent": 100.0 * count / total,
        }
        for frequency, count in ordered[:limit]
    ]


def constant_noise_profile(levels, dominant_peaks):
    """Score how steady a selected frequency band is over a long window."""
    stats = level_statistics(levels)

    if not stats or stats["median"] <= 0:
        return {
            "score": None,
            "verdict": "insufficient_data",
            "stable_percent": None,
            "variation_percent": None,
            "stable_sample_count": 0,
            "sample_count": len(levels),
            "dominant_frequency": None,
            "dominant_share_percent": None,
            "tolerance_db": CONSTANT_NOISE_TOLERANCE_DB,
        }

    tolerance_ratio = math.pow(10.0, CONSTANT_NOISE_TOLERANCE_DB / 20.0)
    lower = stats["median"] / tolerance_ratio
    upper = stats["median"] * tolerance_ratio
    stable_count = sum(1 for level in levels if lower <= level <= upper)
    stable_percent = 100.0 * stable_count / len(levels)
    variance = sum(
        (level - stats["mean"]) * (level - stats["mean"]) for level in levels
    ) / len(levels)
    variation_percent = (
        100.0 * math.sqrt(variance) / stats["mean"]
        if stats["mean"] > 0 else None
    )
    strongest_peak = dominant_peaks[0] if dominant_peaks else None
    dominant_share = (
        strongest_peak["share_percent"] if strongest_peak else 0.0
    )
    score = 0.65 * stable_percent + 0.35 * dominant_share

    if score >= 75.0:
        verdict = "constant"
    elif score >= 50.0:
        verdict = "possible"
    else:
        verdict = "variable"

    return {
        "score": score,
        "verdict": verdict,
        "stable_percent": stable_percent,
        "variation_percent": variation_percent,
        "stable_sample_count": stable_count,
        "sample_count": len(levels),
        "dominant_frequency": (
            strongest_peak["frequency"] if strongest_peak else None
        ),
        "dominant_share_percent": dominant_share,
        "tolerance_db": CONSTANT_NOISE_TOLERANCE_DB,
    }


def window_weather(start, end):
    with database_lock:
        with sqlite3.connect(DATABASE_PATH) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                """
                SELECT outdoor_temperature_c, windy
                FROM weather_samples
                WHERE captured_at >= ? AND captured_at <= ?
                """,
                (start, end),
            ).fetchall()

    if not rows:
        return {
            "sample_count": 0,
            "windy_percent": None,
            "mean_temperature_c": None,
        }

    temperatures = [
        row["outdoor_temperature_c"] for row in rows
        if row["outdoor_temperature_c"] is not None
    ]

    return {
        "sample_count": len(rows),
        "windy_percent": 100.0 * sum(1 for row in rows if row["windy"]) / len(rows),
        "mean_temperature_c": (
            sum(temperatures) / len(temperatures) if temperatures else None
        ),
    }


def analyze_window(start, end, min_hz, max_hz):
    rows, total, stride = sampled_spectrum_rows(start, end, WINDOW_MAX_SAMPLES)
    items = rows_to_points(rows)
    levels = [
        level for level in (
            band_level(item["points"], min_hz, max_hz) for item in items
        )
        if level is not None
    ]
    average_spectrum = average_band_spectrum(items, min_hz, max_hz)
    peak = (
        max(average_spectrum, key=lambda point: point["magnitude"])
        if average_spectrum else None
    )
    dominant_peaks = dominant_histogram(items, min_hz, max_hz)

    return {
        "start": start,
        "end": end,
        "duration_seconds": max(0, int(end - start)),
        "sample_count": total,
        "analyzed_count": len(items),
        "sampling_stride": stride,
        "level": level_statistics(levels),
        "peak_frequency": peak["frequency"] if peak else None,
        "peak_magnitude": peak["magnitude"] if peak else None,
        "dominant_histogram": dominant_peaks,
        "constancy": constant_noise_profile(levels, dominant_peaks),
        "average_spectrum": average_spectrum,
        "series": band_level_series(items, min_hz, max_hz),
        "weather": window_weather(start, end),
    }


def band_changes(test_spectrum, reference_spectrum, limit=BAND_CHANGE_LIMIT):
    """Per-frequency movement between the two windows, strongest rise first."""
    reference_by_frequency = {
        round(point["frequency"], 1): point["magnitude"]
        for point in reference_spectrum
    }
    changes = []

    for point in test_spectrum:
        frequency = round(point["frequency"], 1)

        if frequency not in reference_by_frequency:
            continue

        before = reference_by_frequency[frequency]
        changes.append(
            {
                "frequency": frequency,
                "reference": before,
                "test": point["magnitude"],
                "delta": point["magnitude"] - before,
                "delta_percent": (
                    100.0 * (point["magnitude"] - before) / before
                    if before > 0 else None
                ),
            }
        )

    changes.sort(key=lambda change: change["delta"], reverse=True)
    return changes[:limit]


def compare_windows(test, reference):
    if not test or not reference:
        return None

    if not test["level"] or not reference["level"]:
        return None

    test_level = test["level"]["mean"]
    reference_level = reference["level"]["mean"]
    test_vector = normalize_vector(spectrum_vector(test["average_spectrum"]))
    reference_vector = normalize_vector(
        spectrum_vector(reference["average_spectrum"])
    )

    return {
        "level_delta": test_level - reference_level,
        "level_percent": (
            100.0 * (test_level - reference_level) / reference_level
            if reference_level > 0 else None
        ),
        "delta_db": (
            20.0 * math.log10(test_level / reference_level)
            if reference_level > 0 and test_level > 0 else None
        ),
        "similarity": (
            100.0 * cosine_similarity(test_vector, reference_vector)
            if test_vector and reference_vector else None
        ),
        "changes": band_changes(
            test["average_spectrum"], reference["average_spectrum"]
        ),
    }


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

        if as_bool(current_options().get("publish_ha_entities", True)):
            publish_discovery(client)
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


def publish_discovery(client):
    """Expose the temperature indicators as Home Assistant entities."""
    device = {
        "identifiers": [PUBLISH_PREFIX],
        "name": "Acoustic Observatory",
        "manufacturer": "HA Acoustic Observatory",
        "model": "Add-on",
    }

    for entity in PUBLISHED_ENTITIES:
        key = entity["key"]
        payload = {
            "name": entity["name"],
            "unique_id": f"{PUBLISH_PREFIX}_{key}",
            "state_topic": f"{PUBLISH_PREFIX}/{key}/state",
            "unit_of_measurement": entity["unit"],
            "state_class": entity["state_class"],
            "icon": entity["icon"],
            "availability_topic": f"{PUBLISH_PREFIX}/status",
            "device": device,
        }
        client.publish(
            f"{DISCOVERY_PREFIX}/sensor/{PUBLISH_PREFIX}/{key}/config",
            json.dumps(payload, separators=(",", ":")),
            retain=True,
        )

    client.publish(f"{PUBLISH_PREFIX}/status", "online", retain=True)


def publish_entity_states():
    with publish_lock:
        client = publish_client

    if client is None:
        return

    options = current_options()

    if not as_bool(options.get("publish_ha_entities", True)):
        return

    hours, bucket_minutes = correlation_settings(options)
    correlation = build_correlation(hours, bucket_minutes, exclude_windy=True)
    regression = correlation["regression"]

    with state_lock:
        last_spectrum = state["last_spectrum"]

    summary = summarize_spectrum(last_spectrum)
    values = {
        "low_band_level": round(summary["low_band_magnitude"], 2),
        "temperature_sensitivity": (
            round(regression["slope"], 3) if regression else None
        ),
        "temperature_correlation": (
            round(100.0 * regression["correlation"], 1) if regression else None
        ),
    }

    for key, value in values.items():
        client.publish(
            f"{PUBLISH_PREFIX}/{key}/state",
            "unknown" if value is None else str(value),
            retain=True,
        )


def publish_worker():
    while True:
        try:
            publish_entity_states()
        except Exception as exc:  # pragma: no cover - runtime diagnostics
            with state_lock:
                state["error"] = f"Could not publish Home Assistant entities: {exc}"

        time.sleep(PUBLISH_INTERVAL_SECONDS)


def mqtt_worker():
    global publish_client

    options = load_options()
    with state_lock:
        state["topic"] = options.get("mqtt_topic", DEFAULT_TOPIC)

    while True:
        settings = mqtt_settings()
        with state_lock:
            state["mqtt_source"] = settings.get("source", "fallback")

        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1)
        client.will_set(f"{PUBLISH_PREFIX}/status", "offline", retain=True)

        if settings["username"] and settings["password"]:
            client.username_pw_set(settings["username"], settings["password"])

        if settings["ssl"]:
            client.tls_set()

        client.on_connect = on_connect
        client.on_disconnect = on_disconnect
        client.on_message = on_message

        with publish_lock:
            publish_client = client

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
        temperature_entity = str(
            options.get("outdoor_temperature_entity") or ""
        ).strip()
        configured = bool(speed_entity or gust_entity or temperature_entity)

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
            "temperature_entity": temperature_entity,
            "temperature_configured": bool(temperature_entity),
            "speed_threshold_kmh": speed_threshold,
            "gust_threshold_kmh": gust_threshold,
            "speed_kmh": None,
            "gust_kmh": None,
            "temperature_c": None,
            "windy": False,
            "last_update_at": time.time(),
            "error": None,
        }

        if configured:
            try:
                sample["speed_kmh"] = read_speed_entity(speed_entity)
                sample["gust_kmh"] = read_speed_entity(gust_entity)
                sample["temperature_c"] = read_temperature_entity(temperature_entity)
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
                sample["error"] = f"Could not read weather entities: {exc}"

        with state_lock:
            state["weather"] = sample

        time.sleep(interval)


def cleanup_worker():
    while True:
        options = current_options()
        interval_hours = bounded_int(
            options.get("database_cleanup_interval_hours"),
            DEFAULT_DATABASE_CLEANUP_INTERVAL_HOURS,
            1,
            168,
        )

        try:
            cleanup_database()
        except Exception as exc:  # pragma: no cover - runtime diagnostics
            now = time.time()
            with state_lock:
                state["storage"] = {
                    **state.get("storage", {}),
                    "last_cleanup_at": now,
                    "next_cleanup_at": now + interval_hours * 60 * 60,
                    "error": f"Could not clean database: {exc}",
                }

        time.sleep(interval_hours * 60 * 60)


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
            "storage": snapshot["storage"],
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

    minutes = max(1, min(minutes, MAX_HISTORY_MINUTES))
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


def window_bounds(start_value, end_value):
    """Validate a start/end pair given as epoch seconds. Returns (bounds, error)."""
    start = as_float(start_value)
    end = as_float(end_value)

    if start is None or end is None:
        return None, "Start and end timestamps are required"

    if end <= start:
        return None, "End must be after start"

    if end - start > WINDOW_MAX_SPAN_SECONDS:
        return None, "Window is longer than 31 days"

    return (start, end), None


@app.get("/api/window")
def api_window():
    bounds, error = window_bounds(
        request.args.get("start"), request.args.get("end")
    )

    if error:
        return jsonify({"error": error}), 400

    min_hz = bounded_float(
        request.args.get("min_hz"), DEFAULT_BAND_MIN_HZ, 0.0, 20000.0
    )
    max_hz = bounded_float(
        request.args.get("max_hz"), DEFAULT_BAND_MAX_HZ, 0.0, 20000.0
    )

    if max_hz <= min_hz:
        return jsonify({"error": "max_hz must be above min_hz"}), 400

    reference = None
    has_reference = bool(
        request.args.get("reference_start") and request.args.get("reference_end")
    )

    if has_reference:
        reference_bounds, reference_error = window_bounds(
            request.args.get("reference_start"), request.args.get("reference_end")
        )

        if reference_error:
            return jsonify({"error": f"Reference window: {reference_error}"}), 400

        reference = analyze_window(*reference_bounds, min_hz, max_hz)

    test = analyze_window(*bounds, min_hz, max_hz)

    return jsonify(
        {
            "band": {"min_hz": min_hz, "max_hz": max_hz},
            "test": test,
            "reference": reference,
            "comparison": compare_windows(test, reference),
        }
    )


@app.get("/api/correlation")
def api_correlation():
    options = current_options()
    default_hours, default_bucket_minutes = correlation_settings(options)

    hours = bounded_int(request.args.get("hours"), default_hours, 1, 720)
    bucket_minutes = bounded_int(
        request.args.get("bucket_minutes"), default_bucket_minutes, 1, 120
    )
    exclude_windy = as_bool(request.args.get("exclude_windy", "true"))

    payload = build_correlation(hours, bucket_minutes, exclude_windy)
    payload["temperature_configured"] = bool(
        str(options.get("outdoor_temperature_entity") or "").strip()
    )

    return jsonify(payload)


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


@app.post("/api/sessions/import")
def api_import_session():
    """Label a past window as a campaign, so an event can be reviewed after the fact."""
    payload = request.get_json(silent=True) or {}
    label = str(payload.get("label", "")).strip()
    notes = str(payload.get("notes", "")).strip()

    if not label:
        return jsonify({"error": "Session label is required"}), 400

    bounds, error = window_bounds(
        payload.get("started_at"), payload.get("ended_at")
    )

    if error:
        return jsonify({"error": error}), 400

    started_at, ended_at = bounds

    if ended_at > time.time() + 60:
        return jsonify({"error": "Cannot import a window that is not over yet"}), 400

    with database_lock:
        with sqlite3.connect(DATABASE_PATH) as connection:
            cursor = connection.execute(
                """
                INSERT INTO sessions (label, notes, started_at, ended_at)
                VALUES (?, ?, ?, ?)
                """,
                (label, notes, started_at, ended_at),
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


@app.get("/api/database/export")
def api_export_database():
    """Stream a consistent snapshot of the database, so a copy can be kept off-box."""
    descriptor, name = tempfile.mkstemp(
        prefix=".export-", suffix=".sqlite3", dir=str(DATABASE_PATH.parent)
    )
    os.close(descriptor)
    snapshot = Path(name)

    try:
        with database_lock:
            with sqlite3.connect(DATABASE_PATH) as source:
                with sqlite3.connect(snapshot) as destination:
                    source.backup(destination)
    except (sqlite3.Error, OSError) as exc:
        snapshot.unlink(missing_ok=True)
        return jsonify({"error": f"Could not export the database: {exc}"}), 500

    def stream():
        try:
            with snapshot.open("rb") as handle:
                while True:
                    chunk = handle.read(256 * 1024)

                    if not chunk:
                        break

                    yield chunk
        finally:
            snapshot.unlink(missing_ok=True)

    return Response(
        stream(),
        mimetype="application/octet-stream",
        headers={
            "Content-Disposition": f"attachment; filename={DATABASE_NAME}",
            "Content-Length": str(snapshot.stat().st_size),
        },
    )


if __name__ == "__main__":
    setup_storage()
    threading.Thread(target=mqtt_worker, daemon=True).start()
    threading.Thread(target=weather_worker, daemon=True).start()
    threading.Thread(target=cleanup_worker, daemon=True).start()
    threading.Thread(target=publish_worker, daemon=True).start()
    app.run(host="0.0.0.0", port=8099)
