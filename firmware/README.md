# Atom Echo Firmware

This directory contains the PlatformIO firmware for the M5Stack Atom Echo acoustic sensor.

## What It Does

The firmware:

- connects to Wi-Fi;
- connects to a Mosquitto MQTT broker;
- publishes Home Assistant MQTT discovery messages;
- reads the built-in Atom Echo microphone;
- performs FFT analysis focused on low frequencies;
- publishes normalized acoustic indices and a detailed low-frequency spectrum.

## Local Configuration

Copy the example secrets file:

```bash
cp include/secrets.example.h include/secrets.h
```

Edit `include/secrets.h` with your local Wi-Fi and MQTT credentials.

Do not commit `include/secrets.h`.

## Build and Upload

```bash
pio run
pio run --target upload
pio device monitor
```

## Home Assistant

When MQTT discovery is enabled, Home Assistant should discover a device named `Atom Echo bruit`.

## Notes

The Atom Echo microphone is not calibrated for regulatory acoustic measurement. The published values are relative indicators designed for trend observation and signature detection.
