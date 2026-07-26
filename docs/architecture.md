# Architecture

HA Acoustic Observatory is split into two layers:

1. acquisition firmware running near the sound source or listening point;
2. analysis and visualization tooling integrated with Home Assistant.

## Current Architecture

```text
M5Stack Atom Echo
    |
    | Wi-Fi / MQTT
    v
Mosquitto broker
    |
    v
Home Assistant MQTT integration
```

The firmware currently performs local audio analysis and publishes scalar indicators and a compact low-frequency spectrum.

## Target Architecture

```text
M5Stack Atom Echo or other sensor node
    |
    | MQTT spectrum + indicators
    v
Mosquitto broker
    |
    +--> Home Assistant entities
    |
    +--> Acoustic Observatory add-on
         ├── stores spectrum history
         ├── displays a real-time spectrum
         ├── displays a waterfall
         ├── manages labeled measurement sessions
         └── publishes higher-level detection states
```

## Design Principles

- The firmware should remain reliable and simple.
- MQTT is the boundary between acquisition and analysis.
- The Home Assistant add-on should own historical analysis and heavier visualization.
- The project should support better microphones later without changing the Home Assistant user experience.

## Measurement Notes

The current Atom Echo microphone is useful for trend detection and software development, but it is not a calibrated acoustic measurement microphone. Values are relative and should be interpreted as trends, not certified sound pressure levels.
