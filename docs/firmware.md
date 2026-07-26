# Firmware

The firmware lives in `firmware/` and targets the M5Stack Atom Echo with PlatformIO.

## Requirements

- VS Code
- PlatformIO extension
- M5Stack Atom Echo
- Home Assistant with Mosquitto and the MQTT integration enabled

## Setup

```bash
cd firmware
cp include/secrets.example.h include/secrets.h
```

Edit `include/secrets.h` with your Wi-Fi and MQTT settings.

## Build

```bash
pio run
```

## Upload

```bash
pio run --target upload
```

## Serial Monitor

```bash
pio device monitor
```

The monitor should show Wi-Fi connection, MQTT connection, Home Assistant discovery publishing, and repeated low-frequency analysis blocks.

## Current Signals

The firmware samples audio at 8 kHz, performs a 2048-point FFT, and extracts the low-frequency range used for hum observation.

It publishes:

- RMS level;
- 40-63 Hz index;
- 63-100 Hz index;
- 100-160 Hz index;
- 160-250 Hz index;
- total low-frequency index;
- low-frequency share;
- dominant low-frequency peak;
- detailed spectrum JSON from 20 Hz to 250 Hz.

## Calibration

The 0-100 indices are logarithmic and currently use provisional bounds:

```cpp
constexpr float kIndexFloor = 100.0f;
constexpr float kIndexCeiling = 50000.0f;
```

These should be adjusted after measurements in several environments, such as a quiet room, an office with hard drives, and a location where the target hum is audible.
