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

## Where the Data Lives

The measurement history is a SQLite database stored in the add-on's mapped configuration directory: `/config/acoustic_observatory.sqlite3` inside the container, reachable from the host as `addon_configs/<slug>/acoustic_observatory.sqlite3` through File Editor or Samba.

This matters. Earlier versions kept the database in `/data`, which Home Assistant destroys when an add-on is uninstalled — including when the uninstall dialog offers to keep the add-on data, since that option covers the configuration directory rather than `/data`. A database left there is lost on reinstall, with no warning. Versions from 0.12.0 move it once, automatically, on the first start: the copy is checked to hold the same number of spectra before the original is deleted, and a migration that fails leaves the original in place and reports the problem in the "Base de donnees" panel.

That panel also shows whether the current location survives a reinstall, and offers an **Exporter la base** link that streams a consistent snapshot you can keep off the machine. Home Assistant's own full backups include the add-on data as well, but an export is the quickest independent copy — and the only one you control the timing of.

Retention is enforced at every start and then on the `database_cleanup_interval_hours` cadence, so raising `spectrum_retention_days` only preserves data that has not already been pruned. Raise it before you need the history, not after.

## Analysing a Past Event

Nuisances rarely happen while you are watching the dashboard. When something has already occurred — a test run, a delivery, a night shift — the "Analyse d'un evenement passe" panel measures it after the fact, straight from the stored spectra.

Fill in the event period, then a reference period to compare it against. The *Reference = periode juste avant* button fills the reference with the same duration immediately preceding the event, which is usually the fairest baseline: same day, same weather, same background.

The **frequency band** is the important control. The panel averages only the bins inside the band you select, so choosing the band where the source actually sits sharpens the contrast instead of drowning it in the rest of the spectrum. On a 50 Hz hum, the same two hours can read as a 30 % rise over the full 20-250 Hz range and over 400 % once the band is narrowed onto the peak. Presets cover the common cases:

| Preset | Typical source |
| --- | --- |
| 20-50 Hz | Very low rumble, structure-borne vibration |
| 20-80 Hz | Steady hum, fan and compressor fundamentals |
| 40-120 Hz | Motors and compressors, including the first harmonic |
| 100-250 Hz | Upper bass, casing resonances |
| 20-250 Hz | Everything the firmware publishes |

Set free bounds if none of these fit; the preset switches to *Personnalisee* as soon as you edit a bound.

The results give the mean level of both periods, the gap in absolute, percentage and relative dB terms, the spectral similarity between the two periods, and the frequencies where the level moved most. Two charts show the average spectrum of both periods overlaid, and the band level across the event. If wind was detected during the event, the verdict says so — an increase during a windy period is not evidence about a machine.

Once the analysis looks right, name the period and save it as a campaign. It joins the campaign list with the same CSV export and signature matching as a session recorded live.

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
