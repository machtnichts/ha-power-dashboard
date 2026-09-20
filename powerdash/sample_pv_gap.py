#!/usr/bin/env python3
"""Where does the ~100 W between the Deye and the SDM630 go?

Reads BOTH meters from HA (no direct logger access, so the poller's single session
is not disturbed) and compares the Deye's AC power against the SDM's ACTIVE power
and APPARENT power separately. That distinguishes:

  * Deye reporting apparent power (VA) while the SDM reports active power (W)
    -> Deye_AC ~= SDM_VA, and SDM_VA > SDM_W by the power factor
  * a real load inside the garage branch
    -> Deye_AC ~= SDM_W + garage_loads, with SDM_VA tracking SDM_W closely
  * the Deye simply over-reporting
    -> no consistent relation to either SDM column

Also prints the Deye's DC sum, since DC > AC is the expected conversion loss and
DC == AC would mean the register I use is not the AC output.
"""
import json
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from powerdash.env import ha_token, ha_url

TEMPLATE = (
    '{{ dict('
    'deye_ac=states("sensor.garage_pv_leistung")|float(0),'
    'deye_dc=states("sensor.garage_pv_dc_leistung")|float(0),'
    'l1=states("sensor.sdm630_l1_leistung")|float(0),'
    'l2=states("sensor.sdm630_l2_leistung")|float(0),'
    'l3=states("sensor.sdm630_l3_leistung")|float(0),'
    'l2_va=states("sensor.sdm630_l2_scheinleistung")|float(0),'
    'l2_pf=states("sensor.sdm630_l2_leistungsfaktor")|float(0),'
    'sys=states("sensor.sdm630_systemleistung")|float(0)) | tojson }}'
)
SAMPLES, GAP = 14, 15


def sample():
    req = urllib.request.Request(ha_url() + "/api/template",
                                 data=json.dumps({"template": TEMPLATE}).encode(),
                                 method="POST",
                                 headers={"Authorization": "Bearer " + ha_token(),
                                          "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode())


def main():
    rows = []
    print("%-8s %8s %8s | %8s %8s %8s %8s | %7s %7s" %
          ("time", "deyeAC", "deyeDC", "SDM L1", "SDM L2", "SDM L3", "L2_VA", "PF", "dc/ac"))
    for i in range(SAMPLES):
        v = sample()
        rows.append(v)
        sdm_w = abs(v["l2"])
        print("%-8s %8.1f %8.1f | %8.1f %8.1f %8.1f %8.1f | %7.2f %7.3f"
              % (time.strftime("%H:%M:%S"), v["deye_ac"], v["deye_dc"],
                 v["l1"], v["l2"], v["l3"], v["l2_va"], v["l2_pf"],
                 (v["deye_dc"] / v["deye_ac"]) if v["deye_ac"] else float("nan")))
        if i < SAMPLES - 1:
            time.sleep(GAP)

    ac = [r["deye_ac"] for r in rows if r["deye_ac"] > 50]
    w = [abs(r["l2"]) for r in rows if r["deye_ac"] > 50]
    va = [abs(r["l2_va"]) for r in rows if r["deye_ac"] > 50]
    dc = [r["deye_dc"] for r in rows if r["deye_ac"] > 50]
    if not ac:
        print("\nno usable samples (PV too low)")
        return
    n = len(ac)
    mean = lambda xs: sum(xs) / len(xs)
    print("\nmeans over %d samples with PV > 50 W:" % n)
    print("  Deye AC            : %7.1f W" % mean(ac))
    print("  Deye DC            : %7.1f W   (DC/AC = %.3f)" % (mean(dc), mean(dc) / mean(ac)))
    print("  SDM L2 active (W)  : %7.1f W   (DeyeAC / SDM_W  = %.3f)" % (mean(w), mean(ac) / mean(w)))
    print("  SDM L2 apparent(VA): %7.1f VA  (DeyeAC / SDM_VA = %.3f)" % (mean(va), mean(ac) / mean(va)))
    print("\ninterpretation:")
    print("  DC/AC ~1.05-1.10 confirms 0x0056 is the AC output (loss on top of DC).")
    print("  DeyeAC/SDM_VA near 1.0 would mean the Deye reports VA, not W.")
    print("  DeyeAC/SDM_W near 1.0 would mean both agree and the gap is a real load.")


if __name__ == "__main__":
    main()