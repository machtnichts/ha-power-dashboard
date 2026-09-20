#!/usr/bin/env python3
"""Log SolarEdge vs garage PV side by side, to settle one question with data:

Does the SolarEdge inflate its own PV figure by the garage PV amount?

If it does, then when the SolarEdge is at its 4600 W 1-phase ceiling,

    SE_reported_pv - 4600  ~=  garage_PV

and evcc's homePower (= pv + grid + battery) is already the true house load.
If it does not, the excess stays near zero at saturation and the house figure
genuinely misses the garage PV.

One template call per sample (not /api/states) so a 30 s cadence stays cheap.
"""

import csv
import json
import os
import time
import urllib.request
from pathlib import Path

from powerdash.env import load_env

LOG = Path(__file__).resolve().parent.parent / "logs" / "se_vs_garage.csv"
FIELDS = ["ts", "se_pv_w", "evcc_home_w", "home_total_w", "charger_w", "grid_w",
          "battery_w", "garage_pv_w", "garage_pv_dc_w", "excess_over_4600"]
TEMPLATE = (
    '{{ {"se_pv": states("sensor.evcc_pv_power")|float(0),'
    ' "home": states("sensor.evcc_home_power")|float(0),'
    ' "grid": states("sensor.evcc_grid_power")|float(0),'
    ' "battery": states("sensor.evcc_battery_power")|float(0),'
    ' "charger_kw": states("sensor.evcc_go_e_charger_charge_power")|float(0)} | tojson }}'
)


def sample_from_ha():
    load_env()
    url = os.environ["HASS_URL"].rstrip("/") + "/api/template"
    req = urllib.request.Request(url, data=json.dumps({"template": TEMPLATE}).encode(),
                                 method="POST",
                                 headers={"Authorization": "Bearer " + os.environ["HASS_TOKEN"],
                                          "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode())


def log_sample(garage):
    """Append one comparison row. Never raises: logging must not break publishing."""
    try:
        s = sample_from_ha()
        pv = s.get("se_pv", 0.0)
        home = s.get("home", 0.0)
        charger_w = s.get("charger_kw", 0.0) * 1000.0    # this sensor is in kW
        row = {
            "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
            "se_pv_w": round(pv, 1),
            "evcc_home_w": round(home, 1),
            # evcc's homePower EXCLUDES the wallbox (verified: total = home + charger)
            "home_total_w": round(home + charger_w, 1),
            "charger_w": round(charger_w, 1),
            "grid_w": round(s.get("grid", 0.0), 1),
            "battery_w": round(s.get("battery", 0.0), 1),
            "garage_pv_w": garage.get("ac_power_w"),
            "garage_pv_dc_w": garage.get("dc_power_w"),
            "excess_over_4600": round(pv - 4600.0, 1),
        }
        new = not LOG.exists()
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=FIELDS)
            if new:
                w.writeheader()
            w.writerow(row)
        return row
    except Exception as exc:
        print("  (compare log skipped: %s: %s)" % (type(exc).__name__, exc), flush=True)
        return None