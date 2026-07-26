# Contributing

Thanks for taking an interest in HA Acoustic Observatory.

This project is early-stage. Contributions are welcome, especially around Home Assistant integration, acoustic analysis, documentation, and field testing.

## Project Goals

The project aims to help Home Assistant users observe acoustic trends and recurring low-frequency signatures. It is not intended to replace certified sound level meters or regulatory acoustic reports.

## Development Workflow

1. Open an issue or discussion for larger changes.
2. Create a feature branch from `main`.
3. Keep pull requests focused and reviewable.
4. Document any change that affects MQTT topics, Home Assistant entities, or calibration behavior.

## Firmware Development

The firmware lives in `firmware/` and is built with PlatformIO.

```bash
cd firmware
pio run
```

Secrets must stay local. Copy `firmware/include/secrets.example.h` to `firmware/include/secrets.h` and never commit the real file.

## Home Assistant Add-on Development

The add-on foundation lives in `home-assistant-app/`. It is expected to evolve into a tested Home Assistant OS add-on with Ingress support.

## Coding Style

- Prefer clear names over clever abbreviations.
- Keep firmware changes small and measurable.
- Keep protocol changes backward-compatible when possible.
- Avoid committing generated files, local databases, secrets, or build output.

## Field Data

If you share acoustic measurements, remove private location details first. Raw acoustic data can reveal information about homes, routines, and nearby activity.
