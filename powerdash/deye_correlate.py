#!/usr/bin/env python3
"""Identify the Deye's AC-power register by correlating it with the garage branch.

Ground truth: the SDM630 sits on the garage branch, so its L2 phase carries
(garage loads - garage PV). Sampling the Deye and the SDM630 together and matching
magnitudes identifies the live power register from data instead of a datasheet.

Also checks the DC input pairs (voltage/current in 0.1 units): their sum should
exceed the AC output by roughly the conversion loss - a self-consistency test that
does not depend on any external reference.
"""
import json
import os
import struct
import sys
import time
import urllib.request
from pathlib import Path

from pysolarmanv5 import PySolarmanV5

HOST, SERIAL = "192.168.178.33", 3842831288
BASE, QTY = 0x0050, 0x2D          # 0x0050 .. 0x007C
CANDIDATES = [0x0056, 0x005A]
SAMPLES, GAP = 6, 40


def ha():
    for line in (Path.home() / ".hermes" / ".env").read_text(errors="replace").splitlines():
        line = line.strip()
        if line.startswith("HASS_") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    req = urllib.request.Request(os.environ["HASS_URL"].rstrip("/") + "/api/states",
                                headers={"Authorization": "Bearer " + os.environ["HASS_TOKEN"]})
    with urllib.request.urlopen(req, timeout=20) as r:
        st = json.loads(r.read().decode())
    def g(e):
        try:
            return float(next(s["state"] for s in st if s["entity_id"] == e))
        except Exception:
            return float("nan")
    return {"l2": g("sensor.sdm630_l2_leistung"),
            "l1": g("sensor.sdm630_l1_leistung"),
            "l3": g("sensor.sdm630_l3_leistung"),
            "sdm": g("sensor.sdm630_systemleistung"),
            "chg_w": g("sensor.evcc_go_e_charger_charge_power") * 1000}


def dc_power(vals, base):
    """Sum V*I for the four DC input pairs at 0x006D..0x0074 (0.1 V, 0.1 A)."""
    total = 0.0
    pairs = []
    for off in (0x006D, 0x006F, 0x0071, 0x0073):
        v = vals[off - base] / 10.0
        i = vals[off + 1 - base] / 10.0
        pairs.append((v, i, v * i))
        total += v * i
    return total, pairs


def main():
    inv = PySolarmanV5(HOST, SERIAL, port=8899, mb_slave_id=1, socket_timeout=10)
    rows = []
    for n in range(SAMPLES):
        vals = inv.read_holding_registers(BASE, QTY)
        env = ha()
        dv = {hex(a): vals[a - BASE] for a in CANDIDATES}
        ac_v = vals[0x005B - BASE] / 10.0
        hz = vals[0x005D - BASE] / 100.0
        dcp, pairs = dc_power(vals, BASE)
        rows.append((env, dv, ac_v, hz, dcp))
        print("t%-2d SDM L2=%8.1f W (L1=%7.1f L3=%7.1f, total=%8.1f) charger=%7.0f W"
              % (n, env["l2"], env["l1"], env["l3"], env["sdm"], env["chg_w"]))
        print("    Deye AC=%.1f V  %.2f Hz | 0x0056=%d 0x005A=%d | DC inputs=%s  Σ_DC=%.0f W"
              % (ac_v, hz, dv[hex(0x0056)], dv[hex(0x005A)],
                 " ".join("%.1fV/%.1fA" % (v, i) for v, i, _p in pairs), dcp))
        if n < SAMPLES - 1:
            time.sleep(GAP)

    print("\n=== correlation vs garage branch (|SDM L2|) ===")
    l2 = [abs(r[0]["l2"]) for r in rows]
    for a in CANDIDATES:
        series = [r[1][hex(a)] / 10.0 for r in rows]
        print("  0x%04X as 0.1 W: %s" % (a, " ".join("%.1f" % v for v in series)))
    print("  |SDM L2|       : %s" % " ".join("%.1f" % v for v in l2))
    print("  Σ DC inputs    : %s" % " ".join("%.0f" % r[4] for r in rows))


if __name__ == "__main__":
    sys.exit(main())