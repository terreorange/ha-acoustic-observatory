# MQTT Contract

The firmware publishes retained MQTT state messages under the `atom_echo_noise/` prefix and Home Assistant discovery messages under `homeassistant/sensor/atom_echo_noise/`.

## Availability

```text
atom_echo_noise/status
```

Values:

- `online`
- `offline`

## Scalar State Topics

| Topic | Meaning | Unit |
| --- | --- | --- |
| `atom_echo_noise/audio_rms/state` | Raw RMS level | `RMS` |
| `atom_echo_noise/band_40_63/state` | Normalized 40-63 Hz index | `/100` |
| `atom_echo_noise/band_63_100/state` | Normalized 63-100 Hz index | `/100` |
| `atom_echo_noise/band_100_160/state` | Normalized 100-160 Hz index | `/100` |
| `atom_echo_noise/band_160_250/state` | Normalized 160-250 Hz index | `/100` |
| `atom_echo_noise/low_frequency_energy/state` | Total low-frequency index | `/100` |
| `atom_echo_noise/low_frequency_share/state` | Share of low-frequency energy | `%` |
| `atom_echo_noise/dominant_frequency/state` | Dominant frequency in the low range | `Hz` |
| `atom_echo_noise/dominant_magnitude/state` | Normalized dominant peak index | `/100` |

## Detailed Spectrum

```text
atom_echo_noise/spectrum/state
```

Example payload:

```json
{
  "resolution_hz": 3.9063,
  "bins": {
    "19.5": 120.4,
    "23.4": 98.1,
    "27.3": 110.7
  }
}
```

The spectrum values are relative FFT magnitudes. They are useful for comparison and visualization, but they are not calibrated acoustic units.

## Topics Published by the Add-on

The add-on publishes its own indicators under `ha_acoustic_observatory/` and announces them through Home Assistant MQTT discovery under `homeassistant/sensor/ha_acoustic_observatory/`. Set `publish_ha_entities` to `false` to disable this.

| Topic | Meaning | Unit |
| --- | --- | --- |
| `ha_acoustic_observatory/status` | Add-on availability (`online` / `offline`) | - |
| `ha_acoustic_observatory/low_band_level/state` | Mean 40-250 Hz magnitude of the latest spectrum | `idx` |
| `ha_acoustic_observatory/temperature_sensitivity/state` | Regression slope of level against outdoor temperature | `idx/°C` |
| `ha_acoustic_observatory/temperature_correlation/state` | Pearson correlation between level and temperature | `%` |

The two temperature indicators require `outdoor_temperature_entity` to be configured. They are recomputed every 60 seconds over the `correlation_window_hours` window, always excluding windy periods. When there are fewer than 8 paired buckets, the sensors publish `unknown`.

These values are relative indicators derived from uncalibrated FFT magnitudes. They are meant for trend analysis, not for regulatory acoustic measurement.

## Compatibility Notes

- Topic names should be treated as stable once a public release is tagged.
- Future firmware may add a device ID prefix to support multiple sensors.
- The Home Assistant add-on should consume the detailed spectrum topic directly instead of storing all spectrum bins as Home Assistant sensor attributes.
