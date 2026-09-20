# deyepv (Rust)

A Rust reimplementation of `python -m powerdash.deye_pv`: read the garage PV
(Deye SUN-M160G4 behind its SolarMAN logger) and publish it into Home Assistant.

It is behaviour-compatible on purpose — same topics, same JSON payloads, same
discovery configs, same comparison CSV, same 30 s cadence, same
persistent-connection rule — and that compatibility is measured, not assumed (see
[Verification](#verification)).

The Python poller stays exactly as it is. This is an alternative, not a
replacement.

## Why

This is the one Python service in the setup that is actually **running**, and it
is the one whose failure would be noticed: it holds a virtualenv, a third-party
package (`pysolarmanv5`) and a specific interpreter together. A system change
that disturbs any of those silently removes the garage PV from the Strom
dashboard.

This build has nothing underneath it:

- **no dependencies** — empty `[dependencies]` in `Cargo.toml`; no crates.io, no
  vendored tree, `cargo build --offline` works.
- **one self-contained binary** — the static build links against nothing at all:

  ```
  $ file target/x86_64-unknown-linux-musl/release/deyepv
  ELF 64-bit LSB pie executable, x86-64, statically linked
  ```

The SolarMAN V5 protocol, the Modbus RTU framing and CRC, the HTTP client for
Home Assistant's REST API, the JSON writer and the rotating log are all written
against the Rust standard library. The one libc call is `localtime_r`, because
the CSV timestamp is local time and Rust's std only offers UTC.

## Build and run

```sh
cargo build --release --offline
rustup target add x86_64-unknown-linux-musl            # once
cargo build --release --offline --target x86_64-unknown-linux-musl
```

```sh
deyepv                     # loop, publishing every 30 s
deyepv --once              # one reading, then exit
deyepv --test              # read and publish to powerdash/selftest only
deyepv --discovery         # (re)create the HA entities, retained
deyepv --dry-run           # print the discovery payloads, touch nothing
deyepv --recreate          # delete the entities, wait, recreate, push once
```

Test-only options the Python version hardcodes: `--host`, `--port`, `--serial`,
`--slave-id`, `--interval`, `--timeout`, `--log-csv PATH`, `--no-compare-log`.

Credentials come from `HASS_URL` / `HASS_TOKEN` in the environment or
`~/.hermes/.env`, and are never logged, printed or written anywhere.

## What is identical

| | |
|---|---|
| state topic | `powerdash/garage_pv/state`, retained, same JSON, same key order |
| availability | `powerdash/garage_pv/status` = `online` / `offline`, retained |
| entities | the same five `sensor.garage_pv_*`, same `unique_id`, same `object_id` |
| cadence | 30 s, one persistent session — the logger refuses a second one |
| comparison log | `logs/se_vs_garage.csv`, same columns, same rounding, CRLF |
| register map | one FC3 read of 125 registers from 0x0000 |

The availability behaviour is the subtle one, and it is preserved: a single failed
poll publishes `offline` and a recovery republishes `online`, because retained
availability otherwise leaves the entities stuck unavailable forever. A failed
*publish* also counts as a failed poll.

## One thing worth knowing about the protocol

The V5 frame is asymmetric, which is not obvious from the wire and cost an hour to
establish. A **request** carries the Modbus RTU frame at offset 26:

```
a5 1700 1045 2b00 b8f30ce5 02 0000 00000000 00000000 00000000 | 01030000007d85eb | 26 15
```

A **response** carries it at offset **25** — the prefix is one byte shorter, and the
length field is `14 + len(rtu)` instead of `15 + len(rtu)`:

```
a5 1b00 1015 2a00 b8f30ce5 02 0000 00000000 00000000 00000000 | 010308000a0014...cc | b3 15
```

`tools/reference_frame.py` prints what the reference library builds;
`tools/reference_response.py` builds both candidate response layouts and shows
that only the offset-25 form parses back into correct register values through the
library's own decoder. That is the evidence, and it is why the code has two
different offsets. The decoder does not simply trust the number: it picks the
candidate whose Modbus CRC validates, so a logger variant with the longer prefix
still works.

## Verification

Everything below was actually run. No test connects to the real inverter: it
accepts one session at a time and is currently owned by the Python poller, so a
test that touched it would break the live dashboard.

**1. `cargo test` — 39 tests, all passing**

- 26 unit tests: V5 frame layout and checksum, the CRC16 check vector, sequence
  echo, response decoding including the offset fallback, register decoding and
  scaling, JSON float formatting against Python's `json.dumps`, discovery payload
  text, the CSV row shape, URL parsing, and local time compared against `date(1)`
  (the libc struct layout is the risky part, so it is checked rather than trusted).
- 13 end-to-end tests with a stub logger and a fake Home Assistant: the exact
  published state payload, retained availability, the five discovery configs in
  order, the CSV header and row, byte-identical repeat polls, a dead logger going
  `offline` with no state published, a **rejected publish** going `offline`, a
  failing template call being skipped without breaking the poll, `--dry-run`
  touching neither device nor HA and needing no credentials at all, `--test`
  publishing only to the selftest topic, a **keep-alive frame costing no poll**,
  and the poll interval being honoured rather than approximated.

**2. Differential test against the Python poller — 8/8 messages identical**

```sh
python3 tools/differential_test.py
```

Both implementations get their own stub logger and their own fake Home Assistant
recording every publish; the same command runs on each side and the recorded
messages are compared byte for byte. The Python side is a *copy* with only its
host and port patched, so the original file is untouched.

```
1) one poll (state + availability)
  state and availability     MATCH  powerdash/garage_pv/state
                             MATCH  powerdash/garage_pv/status
2) discovery (five configs + availability)
  discovery payloads         MATCH  homeassistant/sensor/powerdash/garage_pv_power/config
                             ... all six ...
all published messages identical
```

The comparison CSV is checked the same way (`tools/differential_csv.py`): the
Python `log_sample()` and the Rust poller get the same template response and
produce the same row, field for field, including the timestamp format and the
CRLF endings.

**3. `tools/check_python_against_stub.py`** prints the reference library talking to
our stub logger, which is what makes the differential test meaningful: the stub is
good enough for `pysolarmanv5` to read through it, so both sides really are being
fed the same frames.

## One deliberate improvement

The logger occasionally emits a spurious keep-alive frame — control code `0x4710`,
14 bytes — before, or instead of, a reply. This setup sees it every few minutes:
the Python poller's journal shows it as `ERROR Empty:` and once as a crashed
reader thread (`Exception in thread Thread-6 (_data_receiver)`).

The reference library treats such a frame as fatal and reconnects, which throws
away the whole poll and makes the entities go `unavailable` for an interval. Here
the frame is skipped and the real reply is still read:

```
12:44:24   458.7 W AC   478.0 W DC  240.0 V  49.98 Hz    276.65 kWh
  ignoring a keep-alive frame (14 bytes): a50100104700...   <- logged, no blip
12:44:55   469.1 W AC   487.3 W DC  240.0 V  49.99 Hz    276.66 kWh
```

The skipped bytes are printed so an unexpected frame can be identified later. If
no usable reply arrives before the read timeout the poll fails exactly as before,
and a failed poll still takes availability `offline` so a genuinely broken device
cannot masquerade as healthy.

## Known difference

- **https is not supported.** Home Assistant is reached over plain http on the
  local network. TLS would mean a crate, and the whole point here is not needing
  one, so an `https://` URL is refused with an explanatory message instead of
  failing obscurely. If HA ever moves to https, the Python poller still works, or
  a local http reverse proxy can sit in front.

## Cutover

The logger accepts **one session at a time**. Two pollers means both get
timeouts, so this is a one-step switch, and the Python service must be stopped
first:

```sh
# 1. see what the current numbers are
journalctl --user -u powerdash-deye-pv.service -n 5 --no-pager

# 2. stop the Python poller (it holds the only session)
systemctl --user stop    powerdash-deye-pv.service
systemctl --user disable powerdash-deye-pv.service

# 3. read the real device once with the Rust build and compare with step 1
cd /home/adermake/HA-POWER-DASHBOARD/deye-pv-rs
cargo build --release --offline
./target/release/deyepv --once

# 4. if the numbers match, run it as a service
make install
cp deploy/powerdash-deye-pv-rs.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now powerdash-deye-pv-rs.service
```

Step 3 is the one real-device check that could not be done offline, and it is the
last confirmation that the measured protocol layout is right. The AC power should
match the Solarman app within a few watts, and the energy counter should be
unchanged to within a tenth of a kWh.

Rolling back is the same in reverse: `systemctl --user disable --now
powerdash-deye-pv-rs.service`, then enable the Python one. The dashboard needs no
change either way — the entity ids are identical.

## Layout

```
src/main.rs        CLI, poll loop, signal handling, modes
src/v5.rs          SolarMAN V5 framing, Modbus RTU CRC16, response decoding
src/deye.rs        register decoding and the published JSON shape
src/ha.rs          Home Assistant REST client (mqtt.publish, template)
src/json.rs        JSON writer matching Python's json.dumps, plus a small parser
src/discovery.rs   the five entities, their topics and config payloads
src/compare_log.rs the se_vs_garage.csv row
src/logging.rs     timestamps (local time via localtime_r) and console lines
src/env.rs         HASS_URL / HASS_TOKEN from the environment or ~/.hermes/.env
src/bin/stubdeye.rs   stub SolarMAN logger used by the tests
tests/conformance.rs  end-to-end tests
tools/             the differential and reference-frame tools
```
