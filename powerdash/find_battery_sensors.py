#!/usr/bin/env python3
"""Enumerate every battery/charge/discharge entity, to see whether evcc (or the
inverter) exposes charge and discharge separately - and which of them is live."""
import json
import re
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from powerdash.env import ha_token, ha_url

PAT = re.compile(r"batter|charge|discharg|laden|lade|ladezustand|entlad", re.I)


def main():
    req = urllib.request.Request(ha_url() + "/api/states",
                                 headers={"Authorization": "Bearer " + ha_token()})
    with urllib.request.urlopen(req, timeout=25) as r:
        states = json.loads(r.read().decode())

    rows = []
    for s in states:
        eid = s["entity_id"]
        a = s.get("attributes") or {}
        fn = a.get("friendly_name") or ""
        unit = a.get("unit_of_measurement") or ""
        if PAT.search(eid + " " + fn) and unit in ("W", "kW", "A", "%", "kWh", "Wh"):
            rows.append((eid, s["state"], unit, a.get("device_class"), fn,
                         (s.get("last_updated") or "")[:19]))

    print("battery/power-ish entities: %d" % len(rows))
    for eid, st, unit, dc, fn, lu in sorted(rows):
        print("  %-52s %-12s %-5s %-9s %s" % (eid, st, unit, dc or "-", fn))
    print("\n(last_updated shown below for the W/kW ones)")
    for eid, st, unit, dc, fn, lu in sorted(rows):
        if unit in ("W", "kW"):
            print("  %-52s %-10s %s" % (eid, st, lu))


if __name__ == "__main__":
    main()