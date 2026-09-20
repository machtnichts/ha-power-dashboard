#!/usr/bin/env python3
"""Check the units of the power sensors involved, and whether the newly enabled
battery energy counters are live yet.

Units matter here: sensor.evcc_go_e_charger_charge_power reports in kW, not W.
"""
import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from powerdash.env import ha_token, ha_url

WATCH = [
    "sensor.evcc_go_e_charger_charge_power",
    "sensor.evcc_battery_power",
    "sensor.evcc_battery_0_power",
    "sensor.evcc_battery_energy",
    "sensor.evcc_battery_return_energy",
    "sensor.evcc_home_power",
    "sensor.evcc_pv_power",
    "sensor.evcc_grid_power",
    "sensor.evcc_battery_soc",
]


def state(eid):
    req = urllib.request.Request(ha_url() + "/api/states/" + eid,
                                 headers={"Authorization": "Bearer " + ha_token()})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode())


def main():
    vals = {}
    for eid in WATCH:
        try:
            s = state(eid)
            a = s.get("attributes") or {}
            unit = a.get("unit_of_measurement") or ""
            print("%-44s %-16s %-5s class=%s" % (eid, s["state"], unit,
                                                 a.get("state_class") or "-"))
            vals[eid] = (s["state"], unit)
        except Exception as exc:
            print("%-44s ERROR %s" % (eid, exc))

    def num(eid, scale=1.0):
        try:
            return float(vals[eid][0]) * scale
        except Exception:
            return None

    pv = num("sensor.evcc_pv_power")
    grid = num("sensor.evcc_grid_power")
    home = num("sensor.evcc_home_power")
    bat = num("sensor.evcc_battery_power")
    chg = num("sensor.evcc_go_e_charger_charge_power", 1000.0)   # kW -> W
    print("\ninterpreting charger power as kW: %.0f W" % chg if chg is not None else "charger n/a")
    if None not in (pv, grid, home, bat):
        print("pv + grid + battery = %.1f W   vs home = %.1f W   (diff %+.1f)"
              % (pv + grid + bat, home, pv + grid + bat - home))
        print("pv + grid + battery - charger = %.1f W  (diff to home %+.1f)"
              % (pv + grid + bat - (chg or 0), pv + grid + bat - (chg or 0) - home))


if __name__ == "__main__":
    main()