#!/usr/bin/env python3
"""Self-consistency test per device over ONE common window.

For each device: does its cumulative energy counter advance at the rate its own
power reading implies? (delta_kWh / hours -> W)

    implied_W = delta_kWh / hours * 1000

If a device's counter and power disagree, one of the two registers is
misinterpreted (wrong scale, or the field means something else). Comparing the two
devices only makes sense after each is internally consistent.

  SDM630 : sensor.sdm630_gesamt_export_kwh  vs  sensor.sdm630_l2_leistung
  Deye   : sensor.garage_pv_energie         vs  sensor.garage_pv_leistung
"""
import json
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from powerdash.env import ha_token, ha_url

PAIRS = {
    "SDM630 (export counter)": ("sensor.sdm630_gesamt_export_kwh", "sensor.sdm630_l2_leistung"),
    "Deye (lifetime counter)": ("sensor.garage_pv_energie", "sensor.garage_pv_leistung"),
}
HOURS = 2


def history(entity, start):
    url = ("%s/api/history/period/%s?filter_entity_id=%s"
           % (ha_url(), start, entity))
    req = urllib.request.Request(url, headers={"Authorization": "Bearer " + ha_token()})
    with urllib.request.urlopen(req, timeout=45) as r:
        data = json.loads(r.read().decode())
    pts = []
    for series in data:
        for st in series:
            try:
                val = float(st["state"])
            except Exception:
                continue
            ts = st.get("last_updated") or st.get("last_changed")
            pts.append((datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp(), val))
    pts.sort()
    return pts


def value_at(pts, t):
    """Last value at or before t; if the series starts after t, the first value."""
    last = None
    for ts, v in pts:
        if ts <= t:
            last = v
        else:
            break
    if last is None and pts:
        last = pts[0][1]
    return last


def mean_between(pts, t0, t1):
    vals = [v for ts, v in pts if t0 <= ts <= t1]
    if not vals:
        v = value_at(pts, (t0 + t1) / 2)
        return v
    return sum(vals) / len(vals)


def main():
    start = (datetime.now(timezone.utc) - timedelta(hours=HOURS)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
    series = {}
    for label, (e_cnt, e_pw) in PAIRS.items():
        series[label] = (history(e_cnt, start), history(e_pw, start))
        for nm, pts in zip(("counter", "power"), series[label]):
            if pts:
                print("%-26s %-8s %3d records  %s .. %s"
                      % (label, nm, len(pts),
                         datetime.fromtimestamp(pts[0][0]).strftime("%H:%M:%S"),
                         datetime.fromtimestamp(pts[-1][0]).strftime("%H:%M:%S")))
            else:
                print("%-26s %-8s (no records)" % (label, nm))

    # common window across everything
    all_pts = [p for pair in series.values() for pts in pair for p in pts]
    t0 = max(min(pts[0][0] for pts in pair) for pair in series.values())
    t1 = min(max(pts[-1][0] for pts in pair) for pair in series.values())
    hours = (t1 - t0) / 3600.0
    print("common window: %s .. %s  (%.2f h)"
          % (datetime.fromtimestamp(t0).strftime("%H:%M:%S"),
             datetime.fromtimestamp(t1).strftime("%H:%M:%S"), hours))

    for label, (cnt, pw) in series.items():
        c0, c1 = value_at(cnt, t0), value_at(cnt, t1)
        p_mean = mean_between(pw, t0, t1)
        print("\n=== %s ===" % label)
        print("  counter: %.3f -> %.3f  delta %.3f kWh  (%d records)"
              % (c0, c1, c1 - c0, len(cnt)))
        if hours > 0:
            print("  implied power from counter : %8.1f W" % ((c1 - c0) / hours * 1000))
        print("  mean power reading         : %s"
              % ("%8.1f W" % p_mean if p_mean is not None else "n/a"))
        if p_mean and abs(p_mean) > 10:
            print("  ratio counter/power        : %8.2f" % (((c1 - c0) / hours * 1000) / p_mean))


if __name__ == "__main__":
    main()