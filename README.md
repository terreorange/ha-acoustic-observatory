# HA Acoustic Observatory

HA Acoustic Observatory is an experimental acoustic monitoring project for Home Assistant. It started as a low-cost M5Stack Atom Echo sensor built to observe low-frequency hum from refrigeration equipment, but the repository is structured to grow into a complete acoustic observatory: firmware, Home Assistant add-on, documentation, and analysis tools.

The project is not intended to produce regulatory acoustic measurements in dB(A). Its goal is to reveal trends, recurring low-frequency signatures, and changes over time.

## Current Scope

The current working firmware runs on a M5Stack Atom Echo and publishes acoustic indicators to Home Assistant through MQTT:

- raw audio RMS level;
- normalized 0-100 indices for low-frequency bands;
- low-frequency share in percent;
- dominant low-frequency peak;
- a compact JSON spectrum from 20 Hz to 250 Hz.

## Repository Layout

```text
ha-acoustic-observatory/
├── firmware/                 # M5Stack Atom Echo PlatformIO firmware
├── home-assistant-app/        # Future Home Assistant add-on / web observatory
├── docs/                      # Architecture and protocol documentation
├── .github/workflows/         # CI entry points
├── CHANGELOG.md
├── CONTRIBUTING.md
├── LICENSE
└── README.md
```

## Architecture

```text
M5Stack Atom Echo
    |
    | MQTT
    v
Mosquitto broker
    |
    +--> Home Assistant entities
    |
    +--> Future acoustic observatory add-on
         ├── real-time spectrum
         ├── waterfall view
         ├── labeled measurement sessions
         └── signature detection
```

## Firmware Quick Start

1. Open the `firmware/` directory in VS Code with PlatformIO.
2. Copy `firmware/include/secrets.example.h` to `firmware/include/secrets.h`.
3. Fill in Wi-Fi and MQTT settings.
4. Build and upload the firmware to the Atom Echo.
5. Check Home Assistant MQTT discovery for the `Atom Echo bruit` device.

More details are in [docs/firmware.md](docs/firmware.md).

## Home Assistant Add-on

The `home-assistant-app/` directory is a foundation for the future integrated observatory. The intended first version will subscribe to the firmware spectrum topic, store measurements locally, and expose an Ingress web UI inside Home Assistant.

This part is intentionally marked as early-stage until it has been tested on Home Assistant OS.

## MQTT Topics

The firmware publishes retained scalar states under `atom_echo_noise/...` and the detailed spectrum under:

```text
atom_echo_noise/spectrum/state
```

See [docs/mqtt.md](docs/mqtt.md) for the topic contract.

## Roadmap

See [docs/roadmap.md](docs/roadmap.md).

## License

MIT. See [LICENSE](LICENSE).
