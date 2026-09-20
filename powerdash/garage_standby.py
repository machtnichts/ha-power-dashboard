#!/usr/bin/env python3
"""Garage standby residual from HA long-term statistics (robust, no history gaps).

Compares the hourly MEAN of the Deye's AC power against the hourly MEAN of the
SDM630's L2 reading. Averaging also neutralises the Deye's ~4 min cache, which is
what makes instantaneous comparisons unreliable.

A constant difference across power levels = a standby load.
A difference that scales with production = a calibration/scale difference.
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from powerdash.ha_ws import HAWebSocket

DEYE = "sensor.garage_pv_leistung"
SDM = "sensor.sdm630_l2_leistung"
HOURS = 6


def main():
    start = (datetime.now(timezone.utc) - timedelta(hours=HOURS)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
    with HAWebSocket() as ha:
        res = ha.call("recorder/statistics_during_period", start_time=start,
                      statistic_ids=[DEYE, SDM], period="hour",
                      types=["mean", "min", "max"])
        d = {r["start"]: r for r in res.get(DEYE, [])}
        s = {r["start"]: r for r in res.get(SDM, [])}

        print("%-14s %10s %10s %10s %10s" % ("hour", "Deye mean", "SDM mean", "residual", "Deye max"))
        rows = []
        for ts in sorted(set(d) & set(s)):
            dm = d[ts].get("mean")
            sm = s[ts].get("mean")
            if dm is None or sm is None:
                continue
            resid = -sm - dm          # branch export vs inverter production
            rows.append((dm, resid))
            print("%-14s %10.1f %10.1f %+10.1f %10.1f"
                  % (datetime.fromtimestamp(ts / 1000).strftime("%H:%M"),
                     dm, sm, resid, d[ts].get("max") or 0))

        if not rows:
            print("\nno overlapping hourly statistics yet")
            return

        resids = sorted(r for _d, r in rows)
        n = len(resids)
        print("\nresidual (SDM export magnitude - Deye AC):")
        print("  mean %+0.1f W | median %+0.1f W | range %+0.1f .. %+0.1f W"
              % (sum(resids) / n, resids[n // 2], resids[0], resids[-1]))
        lo = [r for d_, r in rows if d_ < 250]
        hi = [r for d_, r in rows if d_ >= 350]
        if lo and hi:
            print("  at Deye <250 W : %+0.1f W (n=%d)" % (sum(lo) / len(lo), len(lo)))
            print("  at Deye >=350 W: %+0.1f W (n=%d)" % (sum(hi) / len(hi), len(hi)))
            print("\n  similar values -> constant offset, i.e. garage standby is plausible")
            print("  different values -> scales with production, i.e. a calibration issue")
        print("\nnote: hourly means include periods when the wallbox drew on that branch,")
        print("      which shows up as a POSITIVE residual (branch importing).")


if __name__ == "__main__":
    main()