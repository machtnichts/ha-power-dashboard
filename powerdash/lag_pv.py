#!/usr/bin/env python3
"""Cross-correlate the Deye's AC power against the SDM630 branch reading.

The Deye's logger serves cached register values (observed frozen at 247.9 W for
3.5 minutes while the SDM630 moved 266 -> 336 W), so a naive instantaneous
comparison mixes a real difference with a stale-read artefact.

Using recorded history for both entities, this finds the lag at which they agree
best. If agreement is good at some lag, the "lost watts" are staleness. A residual
difference that persists at the best lag is a real scale/offset difference.
"""
import json
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from powerdash.env import ha_token, ha_url

DEYE = "sensor.garage_pv_leistung"
SDM = "sensor.sdm630_l2_leistung"
HOURS = 2
MAX_LAG_S = 900
STEP_S = 30


def history(entity, start):
    url = ("%s/api/history/period/%s?filter_entity_id=%s&minimal_response"
           % (ha_url(), start, entity))
    req = urllib.request.Request(url, headers={"Authorization": "Bearer " + ha_token()})
    with urllib.request.urlopen(req, timeout=40) as r:
        data = json.loads(r.read().decode())
    out = []
    for series in data:
        for st in series:
            ts = st.get("last_updated") or st.get("last_changed")
            try:
                val = float(st["state"])
            except Exception:
                continue
            t = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            out.append((t.timestamp(), val))
    out.sort()
    return out


def resample(points, grid):
    """Last value at or before each grid slot (step function)."""
    out, i = [], 0
    last = None
    for slot in grid:
        while i < len(points) and points[i][0] <= slot:
            last = points[i][1]
            i += 1
        out.append(last)
    return out


def main():
    start = (datetime.now(timezone.utc) - timedelta(hours=HOURS)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
    d_pts = history(DEYE, start)
    s_pts = history(SDM, start)
    print("history points: deye=%d  sdm=%d" % (len(d_pts), len(s_pts)))
    if len(d_pts) < 5 or len(s_pts) < 5:
        print("not enough history yet (the Deye sensors only start today)")
        return

    t0 = max(d_pts[0][0], s_pts[0][0])
    t1 = min(d_pts[-1][0], s_pts[-1][0])
    grid = [t0 + i * STEP_S for i in range(int((t1 - t0) / STEP_S))]
    d = resample(d_pts, grid)
    s = resample(s_pts, grid)
    pairs = [(x, -y) for x, y in zip(d, s) if x is not None and y is not None]
    print("usable resampled samples: %d over %.0f min\n" % (len(pairs), (t1 - t0) / 60))

    best = None
    print("%8s %10s %10s" % ("lag(s)", "mean|SDM|", "mean diff (SDM-Deye)"))
    for lag_steps in range(0, MAX_LAG_S // STEP_S + 1):
        a = pairs[lag_steps:]
        if len(a) < 5:
            break
        diffs = [sw - dw for dw, sw in a]
        mean_diff = sum(diffs) / len(diffs)
        mean_sdm = sum(sw for _dw, sw in a) / len(a)
        print("%8d %10.1f %10.1f" % (lag_steps * STEP_S, mean_sdm, mean_diff))
        score = abs(mean_diff)
        if best is None or score < best[2]:
            best = (lag_steps * STEP_S, mean_sdm, score)

    if best:
        print("\nbest lag: %d s  ->  mean |SDM| - Deye = %.1f W" % (best[0], best[2]))
        if best[2] < 15:
            print("=> the two agree once the Deye's cache delay is accounted for;")
            print("   the apparent 'lost watts' are a staleness artefact, not a loss.")
        else:
            print("=> a real difference of about %.0f W remains at the best lag." % best[2])


if __name__ == "__main__":
    main()