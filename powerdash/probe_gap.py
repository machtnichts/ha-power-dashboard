#!/usr/bin/env python3
"""Characterise the house-load gap: is it a constant base load or bursty?

Uses HA's long-term statistics (recorder/statistics_during_period), which survive
recorder purges, and reports how long the cellar heater's entities have been dead.
"""
import json
from datetime import datetime, timedelta

from powerdash.ha_ws import HAWebSocket

DEAD = [
    "sensor.elektroheizungkeller_leistung",
    "sensor.elektroheizungkeller_summe_verbraucht",
    "switch.elektroheizungkeller",
    "sensor.smart_plug_energie_gesamt",
    "switch.smart_plug_steckdose_1",
]
WATCH = ["sensor.evcc_home_power"]


def main():
    with HAWebSocket() as ha:
        states = {s["entity_id"]: s for s in ha.call("get_states")}

        print("=== how long have the dead entities been dead? ===")
        now = datetime.now(tz=None)
        for e in DEAD:
            st = states.get(e)
            if not st:
                print("%-46s NOT PRESENT" % e)
                continue
            lu = st.get("last_updated") or ""
            lc = st.get("last_changed") or ""
            age = ""
            try:
                t = datetime.fromisoformat(lu.replace("Z", "+00:00"))
                age = " (%.1f h ago)" % ((now.astimezone(t.tzinfo) - t).total_seconds() / 3600)
            except Exception:
                pass
            print("%-46s %-12s last_updated=%s%s" % (e, st["state"], lu[:19], age))

        start = (datetime.utcnow() - timedelta(hours=24)).replace(minute=0, second=0,
                                                                 microsecond=0).isoformat() + "Z"
        print("\n=== house power, last 24 h, hourly statistics (W) ===")
        res = ha.call("recorder/statistics_during_period",
                      start_time=start,
                      statistic_ids=WATCH,
                      period="hour",
                      types=["mean", "min", "max"])
        series = res.get("sensor.evcc_home_power", [])
        print("%-22s %9s %9s %9s" % ("hour (start)", "min", "mean", "max"))
        for row in series[-24:]:
            t = datetime.fromtimestamp(row["start"] / 1000).strftime("%d %H:%M")
            def g(k):
                v = row.get(k)
                return "     -   " if v is None else "%9.0f" % v
            print("%-22s %9s %9s %9s" % (t, g("min"), g("mean"), g("max")))
        if series:
            means = [r["mean"] for r in series if r.get("mean") is not None]
            mins = [r["min"] for r in series if r.get("min") is not None]
            if means:
                print("\nmean of hourly means: %.0f W | lowest hourly min: %.0f W | highest hourly max: %.0f W"
                      % (sum(means) / len(means), min(mins), max(r["max"] for r in series if r.get("max") is not None)))


if __name__ == "__main__":
    main()