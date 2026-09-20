# Strom — Lovelace dashboard

A live summary of all electric power consumption in the house.

* **Where:** Home Assistant sidebar → **Strom**, or `/strom-verbrauch`
* **Created via:** the HA websocket API (`lovelace/dashboards/create` +
  `lovelace/config/save`), mode `storage`, so it is editable in the UI like any
  other dashboard. No HA file access and no add-on needed.
* **Existing dashboards are untouched** (meine-energie, dashboard-klima, …).

## The three views

Layout is **table-first**: each group is a markdown card rendering a Jinja table
with right-aligned numeric columns and a computed totals row, rather than tile
cards. Every cell is a live Jinja expression, so the tables update on state change.

**Tabellen**
* Haus — jetzt: Haus gesamt (corrected), SolarEdge raw value, garage branch net,
  wallbox, grid, PV, battery, timestamp
* **PV — beide Anlagen**: both PV sources side by side — power per source, combined
  total, garage PV share, and lifetime yield per source. This is the view the
  SolarEdge app structurally cannot produce: it sees only its own inverter.
* Wallbox & Garage (SDM630): wallbox now, garage net, garage import/export totals,
  lifetime energy into the car
* Garagen-PV (eigener Wechselrichter): DC power, DC→AC efficiency, voltage,
  frequency, lifetime kWh for the Deye
* Steckdosen (alle Messstecker): ranked table — rank, name, power, cumulative kWh,
  share of the instrumented load, plus a Σ totals row and a live/absent count
* Klimaanlagen: room, state, compressor power, kWh today

**Verlauf** — 24 h statistics graphs for house/PV/grid/battery power and a 7-day
graph of cumulative plug energy (graphs stay graphs — a table of 24 h samples
would be unreadable).

**Schalter** — the actual switches next to their sensors, toggleable; this is the
only view with interactive entities, the tables are display-only.

### Pitfall that bit this board once

The markdown card's body field is **`content`**, not `text`. An unknown key does
not raise anywhere visible — HA renders the card as "Konfigurationsfehler", and
because this layout is almost all markdown, the whole board looked broken. The
verifier now checks every card's keys against the documented set (views, sections
and cards separately) before the push, and `entity_id` + `card_size` are set so
the tables only re-render on the entities they read and do not make the layout jump.
`docs/preview.html` (built by `python3 -m powerdash.preview`) renders the real card
bodies to a local file for a layout check that needs no HA login.

## Topology — what measures what (read this before trusting a total)

Verified from evcc's own config (`/home/user/evcc/data/evcc.db`, tables `configs`
and `entities`), not inferred from entity names:

| evcc role | Source |
| :--- | :--- |
| grid (`db:3`) | SolarEdge Hybrid Inverter, 192.168.178.84:1502, usage `grid` |
| pv (`db:4`) | same inverter, usage `pv` |
| battery (`db:5`) | same inverter, usage `battery` |
| charger (`db:1`) | go-e Charger HOME+, 192.168.178.22 |

**evcc never reads the SDM630.** The SDM630 is a separate sub-meter on the
**garage branch only — wallbox + garage PV, both behind it** (user-confirmed).
It sees activity on L2 only; L1 and L3 read 0.00 A.

So:

* `sensor.evcc_home_power` — whole-house consumption as the **SolarEdge** sees it
  (`= pv + grid + battery`). Verified live: the identity holds to 0.00 W.
* `sensor.sdm630_systemleistung` — **the garage branch net only**, not the house.
  Negative = the garage branch is exporting (garage PV producing more than the
  wallbox is drawing) into the house.

### Battery sign convention — measured, not assumed

Battery power is **positive = discharging, negative = charging**. Established
empirically: SOC fell 90.6 % → 90.1 % while the sensor read **+909 W** over 200 s
(a 0.5 % drop on 10 kWh in 200 s ≈ 900 W — two independent indicators agreeing).
The raw register (0xE174) is positive = charging, so evcc presents it negated.

## Where the numbers come from

**Verified identity (to the decimal):**

    Haus ohne Wallbox (evcc homePower) = pv + grid + battery − charger
    Haus gesamt (inkl. Wallbox)        = homePower − SDM630 + charger

The charger part was checked live: 2958.9 W − 1380 W = 1578.9 W = homePower,
difference **+0.0 W** — `evcc_home_power` **excludes the wallbox**.

| Number | Source | Meaning |
| :--- | :--- | :--- |
| Haus gesamt | computed | the real total: homePower − SDM630 + charger |
| SolarEdge-Rohwert | `sensor.evcc_home_power` | house load as the SolarEdge sees it |
| Garagen-Zweig netto | `sensor.sdm630_systemleistung` | garage branch: charger + garage loads − garage PV |
| Wallbox | `sensor.evcc_go_e_charger_charge_power` | **unit is kW**, not W |
| Netz / PV / Batterie | `sensor.evcc_grid_power` / `_pv_power` / `_battery_power` | signed flows |
| Σ Steckdosen | sum of the 17 plug power sensors | only what has a metering plug |
| Garagen-PV | `sensor.garage_pv_leistung` | its own inverter (see below) |

Battery sign is **positive = discharging, negative = charging** (established twice:
SOC fell while the sensor read +909 W, and rose while it read −287 W).

Units bite here: `sensor.evcc_pv_power` is W but
`sensor.evcc_go_e_charger_charge_power` is **kW**. Mixing them produces a phantom
1.4 kW "unaccounted load" — this happened during development.

### Why the SolarEdge's house value is understated (and must be corrected)

The SolarEdge meter sits at the house connection point and sees all import/export
(user-confirmed), but its **generation term contains only its own output**. The
garage PV feeds the house *behind* that meter: it reduces the metered grid flow
without appearing in the generation term, so the SolarEdge's house figure is short
by the garage branch's net contribution. `evcc_home_power` inherits that error.

This was found by the user pointing out the obvious: with a PC, a Linux server, a
router and a TV running, the board's 31–80 W house figure was impossible. The
balance then closes:

    SolarEdge raw                       41.6 W
    Garagen-Zweig netto (SDM630)      −312.2 W   (feeding into the house)
    ─────────────────────────────────────────
    Haus gesamt                        353.8 W
    Summe Messsteckdosen               117.0 W   (plausible subset)

**An earlier version of this document wrongly concluded the opposite** — that the
SolarEdge folds the garage PV into its own PV reading, so the correction must not be
applied. That is retracted: it was based on inference from the inverter's numbers
alone, and the physical balance plus the plug sum disprove it. The correction IS
needed.

**Corollary: a reading you can falsify from the room you are standing in beats any
amount of arithmetic.** The user's "my PC and server are on" was the decisive
measurement.

Still unexplained and tracked separately: the SolarEdge periodically reports PV
power above its own 4600 W 1-phase limit (33 of 336 hourly buckets over 14 days,
peak 5533 W). That anomaly does not affect the derivation above, whose reference
points are the SDM630 and the plug sum, but it means `sensor.evcc_pv_power` is not
trustworthy at saturation. `logs/se_vs_garage.csv` collects `se_pv`, house total,
charger and garage PV every 30 s to look into it.

## Known open points

1. **Both earlier "anomalies" were measurement-basis errors, now fixed.** The old
   "1.2–1.6 kW unaccounted load" was (a) the wallbox, which `homePower` excludes, and
   (b) the garage branch's feed-in, which the SolarEdge cannot see. With
   `Haus gesamt = homePower − SDM630 + charger` the figure lands at 340–355 W, which
   is consistent with the running PC/server/router/TV and above the plug sum.
2. **~~Is the SolarEdge meter at the house connection point?~~** Answered: yes — it
   sees all import/export. That is precisely why a source *behind* it (the garage PV)
   makes its house figure too small.
3. **~~Garage PV attribution~~** Retracted. The SolarEdge does *not* count the garage
   PV as its own output; its house value is simply short by the garage branch net.
   The unrelated remaining question is why it sometimes reports >4600 W (see above).
4. **`sensor.evcc_battery_power` intermittently reads 0 W** while the battery works,
   which breaks the balance identity for as long as it lasts. Enabled alongside it:
   `sensor.evcc_battery_energy` (4678 kWh) and `sensor.evcc_battery_return_energy`
   (6437 kWh) — the charge/discharge *energy* counters, plus
   `sensor.evcc_battery_0_power` (duplicate of the signed power). There is no
   separate charge/discharge *power* pair in evcc. The two energy counters are not on
   the board yet because evcc's naming (energy vs return_energy) is ambiguous about
   which direction is which — label them before trusting them.
5. **Differenz Haus − Steckdosen can go either way** (± sign observed), because the
   two figures come from different measurement chains (SolarEdge vs. Zigbee plugs
   that report on change with ±1–2 W tolerance at low load).
6. **Klimaanlagen show kWh *today*** (`…_energieverbrauch`), not a lifetime counter
   like the plugs — not comparable with the plug column.
7. **`Smart Plug+ / Ventilator Wintergarten-Keller`** has *no* power entity in HA
   (only `sensor.smart_plug_energie_gesamt`), so it cannot appear as a plug.

## Garage PV (Deye SUN-M160G4) — its own inverter

The garage has a separate microinverter that the SolarEdge knows nothing about:

* **Device:** Deye SUN-M160G4-EU-Q0 at **192.168.178.33**, WiFi module MAC
  `40:2a:8f:99:20:f2` (Shanghai High-Flying Electronics — the vendor in these loggers)
* **Protocol:** SolarMAN V5 on TCP **8899** (port 80 is an HTTPD behind auth; 502 is
  closed, so HA's `modbus` integration cannot read it)
* **Logger serial:** the frame field is what the device reports in its replies
  (3842831288). The label value `2404190ABE` is 5 bytes and appears as ASCII at
  registers 0x0003–0x0007 — it is the identity, not the frame serial.
* Register map, scale factors and the identification method:
  see the `deye-solarman-logger` skill.

Created by `powerdash/deye_pv.py`, which reads the logger and publishes through
HA's own `mqtt.publish` service (so it needs no MQTT credentials, only the HA token):

    sensor.garage_pv_leistung      W    AC power
    sensor.garage_pv_dc_leistung   W    DC side, sum of 4 inputs
    sensor.garage_pv_energie       kWh  total_increasing
    sensor.garage_pv_spannung      V
    sensor.garage_pv_frequenz      Hz

Run as a systemd user service:

    systemctl --user status  powerdash-deye-pv.service
    systemctl --user restart powerdash-deye-pv.service
    journalctl --user -u powerdash-deye-pv.service -f

`loginctl enable-linger adermake` is set, otherwise the unit dies with the login
session. Poll interval is 30 s: this logger serves **one session at a time**, and a
fresh connection every 15 s got refused (`NoSocketAvailableError`), which is what
left the entities unavailable. The poller now keeps one connection open.

### Why the SDM630 alone is not enough

The garage branch is a *net* measurement: `charger + garage loads − garage PV`. With
the car idle it does reveal the PV (it reads negative, i.e. feeding in). But when the
car charges on all three phases, the load dwarfs the generation and the net reading
becomes "car importing" — the PV vanishes into the sum. That is why the Deye is read
separately: it is the only source of the garage PV figure, and without it the PV
would be invisible for exactly the hours when it matters most.

The same reasoning applies to the house figure: `Haus gesamt = homePower − SDM630 +
charger` uses the branch as a zone, which stays correct whether that zone is
importing (car charging) or exporting (PV only).

### The 30 m run to the garage has a load-dependent loss

The SDM630 sits in the house, the garage loads are ~30 m away, so the branch
measurement includes the I²R loss of that cable — and it scales with the square of
the current. Copper, 30 m one-way:

| cross-section | PV 1.3 A | 6 A | 16 A | 3-phase 16 A/phase |
| :--- | ---: | ---: | ---: | ---: |
| 1.5 mm² | 1.2 W | 26 W | 186 W | 279 W |
| 2.5 mm² | 0.8 W | 16 W | 114 W | 171 W |
| 4.0 mm² | 0.5 W | 10 W | 71 W | 106 W |

Consequences:

* At PV power levels the loss is **under a watt**, so it does not explain the
  residual — that is garage standby (WiFi router, wallbox standby, door motor), which
  measures ~10–15 W. Confirmed live.
* While the car charges, the loss is tens to hundreds of watts, so the Deye-vs-SDM
  difference **grows with load**. Expect a larger difference at high charging current
  than at midday; do not read that as a metering fault.
* The loss is still genuine house consumption (it heats the cable), so including it
  in the branch total is correct — the formula above needs no correction.

### Three caveats measured on this inverter

1. **Its registers are cached for ~4 minutes.** Polling faster returns the same
   value (only 18 state changes in 71 minutes; the power register sat frozen at
   247.9 W for 3.5 min while the SDM630 moved 266 → 336 W). Never compare a Deye
   reading instantaneously against a live meter — you will find a phantom gap.
2. **Its energy counter is not trustworthy.** Over one common 1.18 h window:
   `SDM630` counter → 297.1 W vs its own power 299.0 W (ratio 0.99,
   self-consistent); `Deye` counter → 42.4 W vs its own power 332.0 W
   (ratio 0.13, contradicts itself). So `0x003F` is mis-scaled or means something
   else, and `sensor.garage_pv_energie` (276.59 kWh) is **unverified** — it is not
   a figure to sum with the SolarEdge yield.
3. **The residual on that branch is garage standby, not a lost 100 W.** The hourly
   average of `|SDM630 L2| − Deye AC` was **−44.5 W** (09:00 hour: Deye 345.3 W vs
   branch 300.8 W), and it is a *constant* offset rather than one that scales with
   production — the signature of a standby load, not a calibration error. The garage
   branch also carries a WiFi router, the wallbox's standby and the garage door
   motor's control electronics, so 10–20 W is expected there; the rest is the Deye's
   own estimate reading a few percent high against the class-rated SDM630.
   `powerdash/garage_standby.py` recomputes this from hourly statistics. The user
   observes ~10–15 W in quiet periods, which matches the standby estimate; the
   44.5 W hourly figure above still contained wallbox activity on that branch.

The **power** register is the well-supported one: `0x0056` sits 3–7 % below the DC
sum (a conversion loss, in the right direction), which rules out a DC/AC mix-up.
Remaining on the board as a live row: `Differenz jetzt`, which is only a snapshot.

## Rebuild / re-push

    cd /home/adermake/HA-POWER-DASHBOARD
    python3 -m powerdash.build_dashboard     # discovery + writes docs/dashboard.json
    python3 -m powerdash.verify_render       # entity existence + Jinja render + cross-check
    python3 -m powerdash.push_dashboard      # create/update the board (idempotent)

`--dry-run` on the push reports without writing.

## Discovery is data-driven

`build_dashboard.py` finds plugs by matching the **friendly names** HA reports,
not by hardcoded entity IDs:

    <base> Leistung          -> power (W)
    <base> Summe verbraucht  -> energy (kWh)
    <base>                   -> switch (toggleable)

So a renamed or replaced plug is picked up or dropped automatically. Only the
SDM630 phase sensors end up "skipped" (they are meter phases, not plugs).

## Files

| File | Purpose |
| :--- | :--- |
| `powerdash/ha_ws.py` | minimal HA websocket client (auth, dashboards, config) |
| `powerdash/build_dashboard.py` | discovery + config generation |
| `powerdash/verify_render.py` | pre-push checks and the Jinja cross-check |
| `powerdash/push_dashboard.py` | create/update the dashboard + read-back verify |
| `powerdash/discover.py` | one-shot audit: every power/energy entity in HA |
| `powerdash/check_evcc.py` | checks the home-power identity |
| `powerdash/probe_battery_sign.py` | establishes the battery sign from SOC movement |
| `powerdash/read_evcc_db.py` | dumps evcc's meter config (which meter reads what) |
| `powerdash/topology.py` | lists PV/meter-ish entities |
| `docs/dashboard.json` | the generated config (the artifact that gets pushed) |
| `logs/discovery.txt` | output of the entity audit |

## Verified / not verified

* Verified: 73/73 entities referenced by the config exist; the Jinja totals card
  renders through HA and its W/kWh/house values match an independent Python sum
  to the decimal; the saved config read back identical (3 views, 31 entities);
  the home-power identity and the battery sign were both confirmed against live
  data rather than assumed.
* **Not verified: how it renders in a browser.** HA requires a login and no
  credentials were available, so the layout was never seen rendered. It uses
  standard `sections` + built-in cards only (no HACS/custom cards are installed),
  so there is no missing-card risk.
* Token hygiene: HASS_TOKEN is read from `~/.hermes/.env` (mode 0600) at
  runtime, never printed and never written into any generated file.
