#!/usr/bin/with-contenv bashio
set -eu

# Ask Home Assistant's declared MQTT service for its internal connection data.
export MQTT_HOST="$(bashio::services mqtt "host")"
export MQTT_PORT="$(bashio::services mqtt "port")"
export MQTT_USERNAME="$(bashio::services mqtt "username")"
export MQTT_PASSWORD="$(bashio::services mqtt "password")"
export MQTT_SSL="$(bashio::services mqtt "ssl")"

exec python /app/app.py
