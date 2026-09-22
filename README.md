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

## Open items

* **Nightly error flood from the Deye poller.** `deye-pv-rs` logs
  `ERROR ConnectionError: ... 192.168.178.33:8899 Host is unreachable` every 33 s between
  dusk and sunrise - around 2600 lines a night for a device that is *expected* to be off
  then: the SolarMAN logger is powered from the inverter, so it leaves the network at dusk
  and comes back after sunrise (last good line 21.09. 17:38 UTC, local sunset 17:40 UTC).
  That noise hides real errors. Options: stay silent between dusk and sunrise (the sun entity,
  or computed), throttle to one line per hour, or one counted summary per night - while an
  unreachable logger at midday must stay an error. **Nothing is implemented on purpose: the
  owner has an idea of his own and will decide.**

## Notes

Private RFC1918 addresses and Home Assistant entity names appear throughout: they describe
*this* installation and are what makes the code readable next to the live dashboard. No
credentials are stored here — the HA token is read from the environment / a 0600 env file,
never from a file in this tree.
