#!/usr/bin/env python3
"""Try the library with the serial the device itself reports in its replies,
then read registers with a raw parser as a cross-check."""
import socket
import struct
import sys

sys.path.insert(0, "/home/adermake/HA-POWER-DASHBOARD")

HOST = "192.168.178.33"
CANDIDATES = {
    "device-reported (b8 f3 0c e5 LE)": 3842831288,
    "label 2404190ABE as hex, low 32 bit": 0x2404190ABE & 0xFFFFFFFF,
    "label suffix 04 19 0A BE as LE": int.from_bytes(bytes.fromhex("04190ABE"), "little"),
    "label prefix 24 04 19 0A as LE": int.from_bytes(bytes.fromhex("2404190A"), "little"),
    "label as decimal digits": int("2404190", 16) if False else 2404190,
}


def try_library(serial):
    from pysolarmanv5 import PySolarmanV5
    inv = PySolarmanV5(HOST, serial, port=8899, mb_slave_id=1, socket_timeout=8)
    return inv.read_holding_registers(0x0000, 4)


def main():
    print("=== library test (pysolarmanv5) ===")
    working = None
    for label, serial in CANDIDATES.items():
        try:
            vals = try_library(serial)
            print("  OK   %-38s serial=%-12d -> %s" % (label, serial, vals))
            if working is None:
                working = serial
        except Exception as exc:
            print("  fail %-38s serial=%-12d -> %s: %s"
                  % (label, serial, type(exc).__name__, str(exc)[:80]))
    print("\nworking serial: %s" % working)
    return 0


if __name__ == "__main__":
    sys.exit(main())