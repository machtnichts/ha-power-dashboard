#!/usr/bin/env python3
"""Decide who is right: the Deye's AC power or the SDM630's branch reading.

Independent evidence: both have cumulative energy counters.
  Deye  sensor.garage_pv_energie        = the inverter's lifetime AC production
  SDM   sensor.sdm630_gesamt_export_kwh = energy that left the garage branch

Over any window:  SDM_export_delta = Deye_production_delta - garage_loads_energy
So if the Deye's energy counting is sound, the two deltas track each other closely
(garage loads are small). If the Deye under-reports power by ~20 %, its ENERGY
counter will lag the SDM's export counter by a similar ratio - which settles the
power question without needing a third meter.
"""
import json
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from powerdash.env import ha_token, ha_url

DEYE_E = "sensor.garage_pv_energie"
SDM_E = "sensor.sdm630_gesamt_export_kwh"
HOURS = 2


def history(entity, start):
    url = ("%s/api/history/period/%s?filter_entity_id=%s"
           % (ha_url(), start, entity))
    req = urllib.request.Request(url, headers={"Authorization": "Bearer " + ha_token()})
    with urllib.request.urlopen(req, timeout=40) as r:
        data = json.loads(r.read().decode())
    out = []
    for series in data:
        for st in series:
            try:
                val = float(st["state"])
            except Exception:
                continue
            ts = st.get("last_updated") or st.get("last_changed")
            out.append((ts, val))
    out.sort()
    return out


def main():
    start = (datetime.now(timezone.utc) - timedelta(hours=HOURS)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
    d = history(DEYE_E, start)
    s = history(SDM_E, start)

    def show(label, pts):
        print("%s: %d records" % (label, len(pts)))
        for ts, v in pts:
            print("   %s  %10.3f" % (ts[11:19], v))
        if len(pts) >= 2:
            print("   delta over window: %.3f kWh" % (pts[-1][1] - pts[0][1]))

    print("=== Deye lifetime AC production ===")
    show("deye", d[-8:] if len(d) > 8 else d)
    print("\n=== SDM630 garage-branch export ===")
    show("sdm", s[-8:] if len(s) > 8 else s)

    if len(d) >= 2 and len(s) >= 2:
        dd = d[-1][1] - d[0][1]
        ds = s[-1][1] - s[0][1]
        print("\nwindow: %s .. %s" % (d[0][0][11:19], d[-1][0][11:19]))
        print("Deye production delta : %8.3f kWh" % dd)
        print("SDM export delta      : %8.3f kWh" % ds)
        if dd > 0.01:
            print("ratio SDM/Deye        : %8.3f" % (ds / dd))
            print("\n  ratio ~1.00 -> both agree; the 50 W gap is a power-display quirk")
            print("  ratio <1    -> the branch consumes (garage loads), expected")
            print("  ratio >1    -> the branch exports MORE than the Deye produces,")
            print("                 so the Deye under-reports its power")


if __name__ == "__main__":
    main()