#!/usr/bin/env python3
"""Find which Deye registers are LIVE measurements.

The whole map the logger exposes is 0x0000-0x007C (125 registers; a read beyond
that returns a Modbus exception). Static settings and live measurements look alike
in one sample, so this reads the block twice with a gap and reports what changed -
only a measurement moves. Cross-checked against the garage branch (SDM630) and the
wallbox from HA at the same moment.
"""
import json
import os
import struct
import sys
import time
import urllib.request
from pathlib import Path

from pysolarmanv5 import PySolarmanV5

HOST = "192.168.178.33"
SERIAL = 3842831288
GAP_S = 120
BASE, QTY = 0x0000, 125


def ha_states():
    for line in (Path.home() / ".hermes" / ".env").read_text(errors="replace").splitlines():
        line = line.strip()
        if line.startswith("HASS_") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    req = urllib.request.Request(os.environ["HASS_URL"].rstrip("/") + "/api/states",
                                headers={"Authorization": "Bearer " + os.environ["HASS_TOKEN"]})
    with urllib.request.urlopen(req, timeout=20) as r:
        st = json.loads(r.read().decode())
    keep = ["sensor.sdm630_systemleistung", "sensor.sdm630_l2_leistung",
            "sensor.evcc_go_e_charger_charge_power", "sensor.evcc_pv_power",
            "sensor.sdm630_gesamt_export_kwh"]
    return {k: next(s["state"] for s in st if s["entity_id"] == k) for k in keep}


def ascii_words(vals, start, count):
    out = ""
    for v in vals[start:start + count]:
        out += struct.pack(">H", v).decode("ascii", errors="replace")
    return out


def main():
    inv = PySolarmanV5(HOST, SERIAL, port=8899, mb_slave_id=1, socket_timeout=10)
    a = inv.read_holding_registers(BASE, QTY)
    ha1 = ha_states()
    print("sample 1 taken; waiting %d s" % GAP_S)
    time.sleep(GAP_S)
    b = inv.read_holding_registers(BASE, QTY)
    ha2 = ha_states()

    print("\nserial (ASCII at 0x0003..0x0007): %r" % ascii_words(a, 3, 5))
    print("\nHA cross-check:")
    for k in ha1:
        print("  %-42s %s -> %s" % (k, ha1[k], ha2[k]))

    print("\n%-8s %10s %10s   %s" % ("addr", "sample1", "sample2", "changed?"))
    for i, (x, y) in enumerate(zip(a, b)):
        addr = BASE + i
        ch = "  <== CHANGED" if x != y else ""
        if x or y or ch:
            asc = ""
            if 0x21 <= (x >> 8) <= 0x7E and 0x21 <= (x & 0xFF) <= 0x7E:
                asc = "  ascii=%r" % struct.pack(">H", x).decode("ascii", "replace")
            print("0x%04X %10d %10d   %s%s" % (addr, x, y, ch, asc))


if __name__ == "__main__":
    sys.exit(main())