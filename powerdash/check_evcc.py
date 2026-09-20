#!/usr/bin/env python3
"""Sanity-check how evcc / the SDM630 relate, so the dashboard's 'house total'
is defined from measured relationships rather than an assumption."""
import json, os, sys, urllib.request
from pathlib import Path

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
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode())

def main():
    st = {s["entity_id"]: s for s in states()}
    def v(eid):
        try:
            return float(st[eid]["state"])
        except Exception:
            return None
    ids = ["sensor.evcc_home_power", "sensor.evcc_pv_power", "sensor.evcc_grid_power",
           "sensor.evcc_battery_power", "sensor.evcc_go_e_charger_charge_power",
           "sensor.sdm630_systemleistung", "sensor.sdm630_l1_leistung",
           "sensor.sdm630_l2_leistung", "sensor.sdm630_l3_leistung"]
    vals = {i: v(i) for i in ids}
    for i in ids:
        print("%-46s %s" % (i.rsplit(".", 1)[1], vals[i]))

    hp, pv, grid, bat, chg = vals["sensor.evcc_home_power"], vals["sensor.evcc_pv_power"], vals["sensor.evcc_grid_power"], vals["sensor.evcc_battery_power"], vals["sensor.evcc_go_e_charger_charge_power"]
    if None not in (hp, pv, grid, bat):
        print("\ncandidate identities for evcc home power:")
        for expr, label in [(pv + grid + bat, "pv + grid + battery"),
                            (pv + grid, "pv + grid"),
                            (pv - bat + grid, "pv - battery + grid"),
                            (grid + bat, "grid + battery")]:
            print("  %-22s = %10.2f   home=%s  diff=%+.2f" % (label, expr, hp, expr - hp))
        print("\nsum of SDM630 phases = %.2f" % sum(filter(None, [vals["sensor.sdm630_l1_leistung"], vals["sensor.sdm630_l2_leistung"], vals["sensor.sdm630_l3_leistung"]])))

    # sign convention of battery power while discharging
    a = st["sensor.evcc_battery_power"].get("attributes", {})
    print("\nbattery_power attrs:", json.dumps({k: a[k] for k in list(a)[:6]}, ensure_ascii=False))

if __name__ == "__main__":
    sys.exit(main())