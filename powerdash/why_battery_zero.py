#!/usr/bin/env python3
"""Why did home = pv + grid + battery stop holding?

Checks the battery sensor's raw state/attributes and whether SOC moves, so we can
tell 'battery really idle' from 'battery sensor reporting 0 while the battery works'.
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from powerdash.env import ha_token, ha_url

WATCH = ["sensor.evcc_battery_power", "sensor.evcc_battery_soc", "sensor.evcc_pv_power",
         "sensor.evcc_home_power", "sensor.evcc_grid_power",
         "sensor.evcc_go_e_charger_charge_power"]


def get_state(eid):
    import urllib.request
    req = urllib.request.Request(ha_url() + "/api/states/" + eid,
                                 headers={"Authorization": "Bearer " + ha_token()})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode())


def main():
    for eid in WATCH:
        s = get_state(eid)
        a = s.get("attributes") or {}
        print("%-46s state=%-12s unit=%-5s class=%-12s last_updated=%s"
              % (eid, s["state"], a.get("unit_of_measurement") or "-",
                 a.get("state_class") or "-", (s.get("last_updated") or "")[:19]))

    soc = get_state("sensor.evcc_battery_soc")["state"]
    bat = get_state("sensor.evcc_battery_power")["state"]
    print("\nsoc=%s battery=%s ; waiting 60 s to see if SOC moves" % (soc, bat))
    time.sleep(60)
    print("soc=%s battery=%s after 60 s" % (get_state("sensor.evcc_battery_soc")["state"],
                                            get_state("sensor.evcc_battery_power")["state"]))


if __name__ == "__main__":
    main()