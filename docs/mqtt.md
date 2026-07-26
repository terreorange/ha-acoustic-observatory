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

## Compatibility Notes

- Topic names should be treated as stable once a public release is tagged.
- Future firmware may add a device ID prefix to support multiple sensors.
- The Home Assistant add-on should consume the detailed spectrum topic directly instead of storing all spectrum bins as Home Assistant sensor attributes.
