#!/usr/bin/env python3
"""Is the SolarEdge's reported PV power ever physically impossible?

A 1-phase inverter limited to 4600 W cannot truly produce more than that, so any
recorded value above the limit is the inverter attributing *someone else's*
generation (the garage PV at the connection-point meter) to itself.

Uses long-term statistics, which survive recorder purges.
"""
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from powerdash.ha_ws import HAWebSocket

PV = "sensor.evcc_pv_power"
GARAGE = "sensor.garage_pv_leistung"
LIMIT = 4600.0
DAYS = 14


def main():
    with HAWebSocket() as ha:
        start = (datetime.utcnow() - timedelta(days=DAYS)).replace(
            minute=0, second=0, microsecond=0).isoformat() + "Z"
        res = ha.call("recorder/statistics_during_period", start_time=start,
                      statistic_ids=[PV, GARAGE], period="hour",
                      types=["mean", "min", "max"])
        pv = res.get(PV, [])
        print("hourly buckets for %s over %d days: %d" % (PV, DAYS, len(pv)))

        over = [(r["start"], r["max"]) for r in pv
                if r.get("max") is not None and r["max"] > LIMIT]
        print("\nbuckets whose MAX exceeds the %.0f W 1-phase limit: %d" % (LIMIT, len(over)))
        for ts, mx in over[-15:]:
            when = datetime.fromtimestamp(ts / 1000).strftime("%d.%m. %H:%M")
            print("   %s  max %8.1f W   (+%.0f above limit)" % (when, mx, mx - LIMIT))

        allmax = [r["max"] for r in pv if r.get("max") is not None]
        allmin = [r["min"] for r in pv if r.get("min") is not None]
        if allmax:
            print("\noverall: min %.0f W  max %.0f W" % (min(allmin), max(allmax)))
            peak = max(pv, key=lambda r: r["max"] or -1e9)
            print("peak bucket: %s max %.0f W"
                  % (datetime.fromtimestamp(peak["start"] / 1000).strftime("%d.%m. %H:%M"),
                     peak["max"]))

        g = res.get(GARAGE, [])
        if g:
            print("\ngarage PV buckets available: %d (data starts today)" % len(g))
            for r in g[-6:]:
                when = datetime.fromtimestamp(r["start"] / 1000).strftime("%d.%m. %H:%M")
                print("   %s  mean %6.1f  max %6.1f W"
                      % (when, r.get("mean") or 0, r.get("max") or 0))


if __name__ == "__main__":
    sys.exit(main())