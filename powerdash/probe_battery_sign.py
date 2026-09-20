#!/usr/bin/env python3
"""Determine the sign convention of evcc battery power empirically, by watching
SOC against battery power. Charging = SOC rises. No guessing from convention.

Also samples the SDM630 (garage branch) and the charger, to see whether the
garage branch is net-importing or net-exporting at the same time.
"""
import json, os, time, urllib.request
from pathlib import Path

IDS = [
    "sensor.evcc_battery_power", "sensor.evcc_battery_soc", "sensor.evcc_pv_power",
    "sensor.evcc_grid_power", "sensor.evcc_home_power",
    "sensor.evcc_go_e_charger_charge_power",
    "sensor.sdm630_systemleistung", "sensor.sdm630_l2_leistung",
]
SAMPLES = 10
DELAY = 20


def load_env():
    for line in (Path.home() / ".hermes" / ".env").read_text(errors="replace").splitlines():
        line = line.strip()
        if line.startswith("HASS_") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def states():
    load_env()
    req = urllib.request.Request(os.environ["HASS_URL"].rstrip("/") + "/api/states",
                                headers={"Authorization": "Bearer " + os.environ["HASS_TOKEN"]})
    with urllib.request.urlopen(req, timeout=25) as r:
        d = json.loads(r.read().decode())
    return {s["entity_id"]: s["state"] for s in d}


def f(d, k):
    try:
        return float(d[k])
    except Exception:
        return float("nan")


def main():
    rows = []
    for i in range(SAMPLES):
        st = states()
        rows.append({k.split(".")[-1]: f(st, k) for k in IDS})
        rows[-1]["t"] = time.strftime("%H:%M:%S")
        print("%s  bat=%8.1f W  soc=%6.2f%%  pv=%7.1f  grid=%7.1f  home=%7.1f  drv=%6.1f  sdm=%7.1f  sdml2=%7.1f"
              % (rows[-1]["t"], rows[-1]["evcc_battery_power"], rows[-1]["evcc_battery_soc"],
                 rows[-1]["evcc_pv_power"], rows[-1]["evcc_grid_power"], rows[-1]["evcc_home_power"],
                 rows[-1]["evcc_go_e_charger_charge_power"], rows[-1]["sdm630_systemleistung"],
                 rows[-1]["sdm630_l2_leistung"]))
        if i < SAMPLES - 1:
            time.sleep(DELAY)

    d_soc = rows[-1]["evcc_battery_soc"] - rows[0]["evcc_battery_soc"]
    mean_bat = sum(r["evcc_battery_power"] for r in rows) / len(rows)
    print("\nSOC change over %ds: %+0.2f%%   mean battery power: %+.1f W" % (SAMPLES * DELAY, d_soc, mean_bat))
    if d_soc > 0.05:
        print("=> SOC ROSE while battery power was %s" % ("negative" if mean_bat < 0 else "positive"))
        print("=> sign: %s = CHARGING" % ("negative" if mean_bat < 0 else "positive"))
    elif d_soc < -0.05:
        print("=> SOC FELL while battery power was %s" % ("negative" if mean_bat < 0 else "positive"))
        print("=> sign: %s = DISCHARGING" % ("negative" if mean_bat < 0 else "positive"))
    else:
        print("=> SOC barely moved - inconclusive from this window (battery near idle)")

    # per-table identity check with the sign we just inferred
    print("\nidentity check home = pv + grid + battery on the last sample:")
    r = rows[-1]
    s = r["evcc_pv_power"] + r["evcc_grid_power"] + r["evcc_battery_power"]
    print("  pv+grid+battery = %.2f  vs home = %.2f  (diff %+.2f)" % (s, r["evcc_home_power"], s - r["evcc_home_power"]))


if __name__ == "__main__":
    main()