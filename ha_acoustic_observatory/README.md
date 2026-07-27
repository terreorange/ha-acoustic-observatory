# Acoustic Observatory Add-on

Home Assistant add-on for HA Acoustic Observatory. It subscribes to the spectrum topic published by the firmware, stores history locally, exposes an Ingress web UI, and publishes higher-level indicators back to Home Assistant.

## Status

Working, but not yet validated on a wide range of Home Assistant OS installations.

## Configuration

| Option | Meaning | Default |
| --- | --- | --- |
| `mqtt_topic` | Spectrum topic to subscribe to | `atom_echo_noise/spectrum/state` |
| `wind_speed_entity` | Home Assistant wind-speed entity | empty |
| `wind_gust_entity` | Home Assistant wind-gust entity | empty |
| `outdoor_temperature_entity` | Home Assistant outdoor-temperature entity | empty |
| `wind_speed_threshold_kmh` | Above this, a sample is flagged windy | `15` |
| `wind_gust_threshold_kmh` | Above this, a sample is flagged windy | `30` |
| `weather_poll_interval_seconds` | Weather polling cadence | `30` |
| `correlation_window_hours` | Window used for the heat-noise analysis | `48` |
| `correlation_bucket_minutes` | Averaging interval for paired points | `10` |
| `publish_ha_entities` | Publish add-on sensors through MQTT discovery | `true` |
| `spectrum_retention_days` | Spectrum history retention | `14` |
| `weather_retention_days` | Weather history retention | `30` |
| `database_cleanup_interval_hours` | Cleanup cadence | `6` |

Wind and temperature entities are read through the Supervisor API, so any unit is accepted: speeds in km/h, m/s, mph or knots, and temperatures in °C, °F or K are converted automatically.

## Heat and Noise Analysis

Refrigeration plants modulate with outdoor temperature: the hotter it gets, the more the compressors and condenser fans run, and the stronger the low-frequency hum. The "Chaleur et bruit" panel makes that dependency visible.

Set `outdoor_temperature_entity` to an outdoor sensor, then let the add-on collect data for at least a full day-night cycle. The panel averages the acoustic level and the temperature over the same time buckets, plots one point per bucket, and fits a trend line.

Two numbers summarize the result:

- **Thermal sensitivity** — the slope, in index points per degree Celsius. How much louder the low band gets per degree.
- **Correlation** — the Pearson coefficient, as a percentage. How consistently the two move together. Above roughly 60 %, the relationship is hard to attribute to chance.

Windy buckets are excluded by default, and shown as faded points when included. Wind raises the low-frequency level on its own, so leaving it in weakens the fit and invites the objection that you are simply measuring the weather.

## Home Assistant Entities

With `publish_ha_entities` enabled, the add-on creates a device named *Acoustic Observatory* carrying three sensors: low-band level, thermal sensitivity, and heat-noise correlation. Home Assistant keeps long-term statistics for them, so you can chart months of data and trigger automations.

A minimal dashboard card:

```yaml
type: vertical-stack
cards:
  - type: statistic
    entity: sensor.acoustic_observatory_correlation_chaleur_bruit
    stat_type: mean
    period:
      calendar:
        period: day
  - type: statistics-graph
    title: Niveau grave et temperature
    entities:
      - sensor.acoustic_observatory_niveau_bande_basse
      - sensor.outdoor_temperature
    stat_types:
      - mean
      - max
    days_to_show: 30
```

Check the exact entity ids under Settings, Devices and services, MQTT, after the add-on has connected once.

## Limitations

The sensor is not a calibrated sound level meter, and the microphone response below 50 Hz is unknown. Absolute levels carry no regulatory weight. What the add-on produces is evidence of a *pattern*: a stable low-frequency signature that tracks outdoor temperature, persists at night, and does not follow the wind. Use it to decide when a calibrated measurement is worth commissioning.
