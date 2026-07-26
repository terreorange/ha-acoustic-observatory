# Changelog

All notable changes to this project will be documented in this file.

The format follows the spirit of Keep a Changelog, and this project uses semantic versioning once releases begin.

## [Unreleased]

### Added

- Repository foundation for the firmware and future Home Assistant add-on.
- MIT license.
- Contribution guide.
- Architecture, MQTT, firmware, and roadmap documentation.

### Changed

- Moved the existing PlatformIO firmware into `firmware/`.
- Reframed the repository as the full HA Acoustic Observatory project.

## [0.1.0] - 2026-07-26

### Added

- Initial M5Stack Atom Echo firmware.
- MQTT connection to Mosquitto.
- Home Assistant MQTT discovery.
- Low-frequency FFT analysis from the Atom Echo microphone.
- Normalized 0-100 indices for low-frequency bands.
- JSON spectrum publishing from 20 Hz to 250 Hz.
