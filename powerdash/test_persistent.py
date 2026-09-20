#!/usr/bin/env python3
"""Prove the persistent connection survives repeated reads (the failure mode was
a fresh connection every poll). Reads 4x with a short gap in ONE process."""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from powerdash.deye_pv import read_deye

for i in range(4):
    try:
        d = read_deye()
        print("read %d: %6.1f W AC  %6.1f W DC  %5.1f V  %5.2f Hz"
              % (i + 1, d["ac_power_w"], d["dc_power_w"], d["ac_voltage_v"],
                 d["frequency_hz"]), flush=True)
    except Exception as exc:
        print("read %d: FAILED %s: %s" % (i + 1, type(exc).__name__, exc), flush=True)
    if i < 3:
        time.sleep(8)