# HA-POWER-DASHBOARD

Two things that belong together because they feed one view:

1. **`docs/`** — the "Strom" Lovelace dashboard for Home Assistant: a live summary of all
   electric power in the house (consumption, PV, battery, wallbox, individual circuits).
   It is a Jinja/table-first dashboard, created and updated through the HA websocket API
   in `storage` mode, so it stays editable in the UI and needs no HA file access.
   `docs/dashboard.json` is the dashboard payload, `docs/preview.html` a local preview.
2. **`powerdash/` + `deye-pv-rs/`** — the garage PV (Deye SUN-M160G4 behind its SolarMAN
   logger), read locally and published into Home Assistant via MQTT so it appears in that
   dashboard.

## The two pollers

* **`powerdash/deye_pv.py`** (Python, `python3 -m powerdash.deye_pv`) — the original
  poller. It is the one actually running today, and it depends on a virtualenv and the
  third-party `pysolarmanv5` package.
* **`deye-pv-rs`** (Rust) — a reimplementation of exactly that poller, built as a static
  binary with no runtime dependencies, so a system or Python change cannot silently take
  the garage PV off the dashboard. It is behaviour-compatible on purpose: same MQTT
  topics, same JSON payloads, same discovery configs, same 30 s cadence, same
  persistent-connection rule — and that compatibility is measured against the Python
  version rather than assumed (see `deye-pv-rs/README.md` → Verification).

The Python poller stays as it is; the Rust build is an alternative, not a replacement.

## Layout

```
deye-pv-rs/       Rust poller (src, tests with a stub SolarMAN server, tools, deploy unit)
powerdash/        Python package: the poller, the dashboard builder and the probes
docs/             dashboard definition (dashboard.json), README, local preview
deploy/           systemd user unit for the Python poller
logs/             runtime logs (not tracked)
```

## Running and testing

```sh
# Python poller (as the service runs it)
.venv/bin/python -m powerdash.deye_pv

# Rust poller
cd deye-pv-rs && cargo test && make static

# Dashboard rebuild
python3 -m powerdash.build_dashboard
```

Reads are deliberately gentle: one persistent connection to the logger, one poll cycle
per 30 s, no retry storms. The logger tolerates only a limited number of sessions, so a
second client is never started while the first is alive.

## Related repositories

* the charging controller of the same house (go-e wallbox, SolarEdge surplus)
* the Modbus proxy that serves the inverter's single session to several readers

## Behaviour worth knowing

* **The Deye poller backs off while the logger is away.** The logger is powered by the
  inverter, so it leaves the network at dusk and returns after sunrise. Retrying it every
  30 s produced ~2600 log lines *and* as many `offline` availability messages per night for a
  device that was only asleep - which buried the errors that matter. The poller now doubles
  its wait from the interval up to 15 minutes, logs a failure only at the start of a streak
  and then every eighth step, and publishes the retained `offline` once per outage (the
  recovery line reports how many attempts it cost). While backing off it watches the garage
  meter (`sensor.sdm630_total_kwh`) through Home Assistant and polls again as soon as energy
  flows there - the inverter exporting, or the car charging - so sunrise is caught within a
  minute. An unreachable logger in bright daylight still reads as an error. Flags and the
  schedule: `deye-pv-rs/README.md`.

## Notes

Private RFC1918 addresses and Home Assistant entity names appear throughout: they describe
*this* installation and are what makes the code readable next to the live dashboard. No
credentials are stored here — the HA token is read from the environment / a 0600 env file,
never from a file in this tree.
