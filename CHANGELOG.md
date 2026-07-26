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

## [0.7.0] - 2026-07-26

### Added

- Browser-side calibration offset for estimated dB(A), dB(B), and dB(C) readings.
- Calibration controls to enter a manual dB correction or align the current estimated dB(C) reading with an external sound level meter.
- Calibrated weighted history views using the configured offset.

### Notes

- The calibration is stored in the browser and applies a simple level offset. It improves trend readability but does not make the Atom Echo a certified sound level meter.

## [0.6.0] - 2026-07-26

### Added

- Selectable history view for estimated weighted readings.
- Time-series history for estimated dB(A), dB(B), dB(C), and C-A gap.
- Spectrum history remains available as the default waterfall view.

### Notes

- Historical weighted readings are still relative, uncalibrated estimates intended for trend comparison.

## [0.5.0] - 2026-07-26

### Added

- Estimated A, B, and C weighted level cards in the Home Assistant add-on UI.
- Estimated `C-A` gap card to highlight low-frequency dominance.
- Plain-language bass reading based on the estimated `C-A` gap.

### Notes

- Weighted readings are relative, uncalibrated estimates based on the received spectrum. They are intended for trend analysis, not regulatory sound-level measurement.

## [0.4.0] - 2026-07-26

### Added

- Measurement sessions with labels and optional notes.
- Session start and stop controls in the Home Assistant add-on UI.
- Session summaries with duration, sample count, average dominant frequency, and signature status.
- CSV export for completed measurement sessions.
- First spectral signature comparison against completed sessions.
- Nuisance score based on completed sessions whose label mentions nuisance, entrepot, ronron, group, or cold-room context.

## [0.3.0] - 2026-07-26

### Added

- Persistent SQLite storage for received spectra in the Home Assistant add-on.
- `/api/history` endpoint returning recent spectrum measurements.
- Waterfall history view with selectable windows for 5 minutes, 10 minutes, 30 minutes, and 1 hour.

## [0.2.3] - 2026-07-26

### Fixed

- The add-on now obtains its MQTT host, port, username, password, and TLS setting from Home Assistant's declared MQTT service at startup.
- The application now prefers those service settings over direct Supervisor and fallback configuration.

## [0.2.2] - 2026-07-26

### Fixed

- Home Assistant add-on now explicitly enables Supervisor API access so it can read the internal MQTT service credentials.
- MQTT authentication failures now explain whether the add-on was using Supervisor-provided credentials or fallback settings.

## [0.2.1] - 2026-07-26

### Fixed

- MQTT broker connection now reads the Home Assistant Supervisor `mqtt` service configuration automatically.
- MQTT callbacks now use the stable Paho callback API v1 to avoid callback signature drift.

## [0.2.0] - 2026-07-26

### Added

- Real-time spectrum UI for the Home Assistant add-on.
- Responsive SVG chart for the 20-250 Hz spectrum.
- Dominant frequency, message count, last update, and MQTT status cards.
- Clear empty-state and stale-data messages when no spectrum has been received.

### Fixed

- MQTT disconnect callback compatibility with Paho MQTT v2.

## [0.1.0] - 2026-07-26

### Added

- Initial M5Stack Atom Echo firmware.
- MQTT connection to Mosquitto.
- Home Assistant MQTT discovery.
- Low-frequency FFT analysis from the Atom Echo microphone.
- Normalized 0-100 indices for low-frequency bands.
- JSON spectrum publishing from 20 Hz to 250 Hz.
