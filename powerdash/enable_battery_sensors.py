#!/usr/bin/env python3
"""Enable the battery entities evcc ships disabled, then compare them against the
signed sensor to find out why sensor.evcc_battery_power sometimes reads 0.

Read-only in effect: enabling a registry entity creates no side effects on hardware.
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from powerdash.ha_ws import HAWebSocket

ENABLE = [
    "sensor.evcc_battery_0_power",
    "sensor.evcc_battery_energy",
    "sensor.evcc_battery_return_energy",
]
WATCH = ["sensor.evcc_battery_power", "sensor.evcc_battery_0_power",
         "sensor.evcc_battery_soc"]


def main():
    with HAWebSocket() as ha:
        for eid in ENABLE:
            try:
                ha.call("config/entity_registry/update", entity_id=eid, disabled_by=None)
                print("enabled %s" % eid)
            except Exception as exc:
                print("could not enable %s: %s" % (eid, str(exc)[:120]))
        time.sleep(8)
        states = {s["entity_id"]: s for s in ha.call("get_states")}

        print("\n%-38s %-12s %s" % ("entity", "state", "unit"))
        for eid in ENABLE + ["sensor.evcc_battery_energy",
                             "sensor.evcc_battery_return_energy"]:
            s = states.get(eid)
            if s:
                print("%-38s %-12s %s" % (eid, s["state"],
                                          (s.get("attributes") or {}).get("unit_of_measurement") or ""))
            else:
                print("%-38s (not in states yet)" % eid)

        print("\nsampling every 10 s to catch the 0 W dropouts:")
        for i in range(6):
            states = {s["entity_id"]: s for s in ha.call("get_states")}
            row = []
            for eid in WATCH:
                s = states.get(eid)
                row.append("%s=%s" % (eid.split(".")[-1], s["state"] if s else "?"))
            print("  %s" % "  ".join(row), flush=True)
            if i < 5:
                time.sleep(10)


if __name__ == "__main__":
    main()