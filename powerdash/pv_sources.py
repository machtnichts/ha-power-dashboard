#!/usr/bin/env python3
"""Which PV energy/power figures exist per source, and what do they actually mean?

Both PV sources must be compared like with like, so check state_class and unit
before putting an "energy so far" column next to each other.
"""
import json
import re
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from powerdash.env import ha_token, ha_url

PAT = re.compile(r"pv|solar|erzeug|generation", re.I)


def main():
    req = urllib.request.Request(ha_url() + "/api/states",
                                 headers={"Authorization": "Bearer " + ha_token()})
    with urllib.request.urlopen(req, timeout=25) as r:
        states = json.loads(r.read().decode())

    print("%-58s %-14s %-6s %-16s %s" % ("entity", "state", "unit", "state_class", "name"))
    for s in sorted(states, key=lambda x: x["entity_id"]):
        eid, a = s["entity_id"], (s.get("attributes") or {})
        unit = a.get("unit_of_measurement") or ""
        if not PAT.search(eid + " " + (a.get("friendly_name") or "")):
            continue
        if unit not in ("W", "kW", "kWh", "Wh", "MWh", "%", ""):
            continue
        if "charger" in eid or "go_e" in eid or "tariff" in eid:
            continue
        print("%-58s %-14s %-6s %-16s %s"
              % (eid, s["state"], unit, a.get("state_class") or "-",
                 a.get("friendly_name") or ""))
        if eid in ("sensor.evcc_pv_energy", "sensor.evcc_stat_total_solar_k_wh_template",
                   "sensor.garage_pv_energie", "sensor.sdm630_gesamt_export_kwh"):
            print("      attrs: %s" % json.dumps(
                {k: a[k] for k in a if k in ("last_reset", "state_class", "unit_of_measurement")},
                ensure_ascii=False))


if __name__ == "__main__":
    main()