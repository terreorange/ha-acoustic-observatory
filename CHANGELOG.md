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

## [0.12.0] - 2026-07-27

### Fixed

- The database lived in `/data`, which Home Assistant destroys when an add-on is uninstalled. Keeping the add-on data at uninstall did not protect it, because that option covers the mapped `addon_config` directory, which this add-on declared but never used. The database now lives in that mapped directory and survives an uninstall and reinstall.

### Added

- Automatic one-time migration of a database left in `/data` by an earlier version. The copy is verified to hold the same number of spectra before the original is removed; a failed migration leaves the original untouched and is reported in the interface.
- `GET /api/database/export` streaming a consistent snapshot of the database.
- "Base de donnees" panel showing where the database is stored, whether it survives a reinstall, the retention in force, the last cleanup, and an export link.

### Notes

- If the mapped configuration directory is unavailable, the add-on keeps using `/data` and says so in the interface rather than failing to start.
- The stored location is now reachable from File Editor or Samba under `addon_configs/`, so the database can be copied without a terminal.

## [0.11.0] - 2026-07-27

### Added

- `GET /api/window` endpoint analysing an arbitrary past time window, with an optional reference window and a configurable frequency band.
- "Analyse d'un evenement passe" panel: pick the event period, a reference period, and the frequency band to weigh, then compare the two.
- Frequency band presets from 20-50 Hz to 20-250 Hz, plus free minimum and maximum bounds.
- Per-frequency comparison table showing where the level moved most between the two periods.
- `POST /api/sessions/import` labelling a past window as a campaign, which makes CSV export and signature matching available after the fact.
- History windows of 3 h, 6 h, 12 h and 24 h.

### Fixed

- `GET /api/history` returned only the newest 720 rows of the requested window, so any request beyond roughly an hour silently showed only its tail. Long windows are now stride-sampled across the whole span.

### Notes

- Window statistics are computed on at most 3000 spectra; beyond that the window is stride-sampled, and the ratio is reported in the interface.
- The band level is the mean magnitude of the bins inside the selected band, so narrowing the band onto a suspected source raises the measured contrast instead of diluting it in the rest of the spectrum.
- Level deltas in dB are ratios of relative FFT magnitudes, not calibrated acoustic levels.

## [0.10.0] - 2026-07-27

### Added

- Optional Home Assistant outdoor-temperature entity configuration.
- Storage of outdoor temperature alongside each weather sample.
- `GET /api/correlation` endpoint pairing acoustic level and temperature over shared time buckets, with least-squares slope and Pearson correlation.
- "Chaleur et bruit" panel plotting outdoor temperature against low-band level, with a trend line and a plain-language verdict.
- Outdoor temperature, thermal sensitivity, and heat-noise correlation tiles.
- Home Assistant MQTT discovery for three add-on sensors: low-band level, thermal sensitivity, and heat-noise correlation.

### Notes

- The correlation analysis excludes windy buckets by default, since wind inflates the low-frequency level and weakens the fit.
- Thermal sensitivity is expressed in index points per degree Celsius. It is a relative trend indicator, not a calibrated acoustic measurement.
- Existing databases are migrated in place by adding the `outdoor_temperature_c` column.

## [0.9.0] - 2026-07-26

### Added

- Configurable retention for stored spectrum samples.
- Configurable retention for stored weather and wind samples.
- Automatic SQLite cleanup worker to prevent long-running add-on storage growth.
- Storage cleanup status in the add-on API.

### Notes

- Default retention keeps 14 days of spectrum samples and 30 days of weather samples.
- The database cleanup runs every 6 hours by default and compacts the SQLite file after deleting old rows.

## [0.8.0] - 2026-07-26

### Added

- Optional Home Assistant wind-speed and wind-gust entity configuration.
- Automatic wind detection based on configurable km/h thresholds.
- Wind status card in the add-on UI.
- Wind overlays in spectrum and weighted-history charts.
- Persistent weather sampling in SQLite for recent history overlays.

### Notes

- This first automatic wind version uses existing Home Assistant entities. Configure `wind_speed_entity` and optionally `wind_gust_entity` in the add-on options.

## [0.7.2] - 2026-07-26

### Changed

- Removed the obsolete B-weighting estimate from the Home Assistant add-on UI.
- Kept the display focused on estimated dB(A), estimated dB(C), and the C-A gap.

## [0.7.1] - 2026-07-26

### Fixed

- The calibration apply action now also calibrates from the sound level meter field when a reference dB(C) value is entered.
- Waterfall and weighted-history time labels now have more left margin and are right-aligned to avoid being clipped.

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
- Time-series history for estimated dB(A), estimated dB(C), and C-A gap.
- Spectrum history remains available as the default waterfall view.

### Notes

- Historical weighted readings are still relative, uncalibrated estimates intended for trend comparison.

## [0.5.0] - 2026-07-26

### Added

- Estimated A and C weighted level cards in the Home Assistant add-on UI.
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
