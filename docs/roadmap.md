# Roadmap

## v0.1 - Firmware Foundation

- M5Stack Atom Echo firmware.
- Wi-Fi and MQTT connection.
- Home Assistant MQTT discovery.
- FFT-based low-frequency indicators.
- Detailed spectrum publishing.

## v0.2 - Repository Foundation

- Split firmware and Home Assistant add-on directories.
- Add project documentation.
- Add MIT license, changelog, and contribution guide.
- Add CI entry point for firmware builds.

## v0.3 - Home Assistant Observatory Add-on

- Subscribe to the spectrum MQTT topic.
- Store spectrum history locally.
- Expose an Ingress web UI in Home Assistant.
- Display current spectrum and waterfall.
- Export CSV sessions.

## v0.4 - Field Measurement Workflow

- Label sessions: quiet room, office, audible nuisance, outdoor reference.
- Compare signatures between sessions.
- Add data retention settings.
- Add simple calibration helpers for 0-100 indices.

## v0.10 - Heat Correlation

- Read an outdoor temperature entity and store it with each weather sample.
- Correlate low-band level against outdoor temperature over shared time buckets.
- Plot the relationship with a trend line, excluding windy periods.
- Publish level, thermal sensitivity, and correlation as Home Assistant sensors.

## v0.5 - Detection

- Create a hum detection score.
- Publish `binary_sensor` and `sensor` entities for nuisance detection.
- Detect recurring low-frequency signatures.
- Add alert examples for Home Assistant automations.

## v1.0 - Stable Release

- Stabilize MQTT topic contract.
- Test add-on installation on Home Assistant OS.
- Document hardware recommendations.
- Add screenshots and example dashboards.
