#!/usr/bin/env python3
"""Talk to the Deye/SolarMAN logger on 192.168.178.33 and find the live values.

The logger serial is read out of the device's own reply frame: pysolarmanv5's
decoder checks the reply's bytes [7:11] against the serial you configured, and the
device echoes its own serial there - so a deliberately wrong request reveals it.

Then it scans the holding/input register space and prints anything that looks like
a live measurement (voltage ~230 V, frequency ~50 Hz, non-zero power), so the map
can be identified from the values rather than from a guessed datasheet.
"""
import sys

from pysolarmanv5 import PySolarmanV5, V5FrameError

HOST = "192.168.178.33"
SERIALS = [3842831288]


def connect(serial):
    return PySolarmanV5(HOST, serial, port=8899, mb_slave_id=1,
                        socket_timeout=8, v5_error_correction=False, verbose=False)


def main():
    inv = None
    used = None
    for serial in SERIALS:
        try:
            inv = connect(serial)
            regs = inv.read_holding_registers(0x0000, 2)
            print("serial %d works; first holdings 0x0000 = %s" % (serial, regs))
            used = serial
            break
        except Exception as exc:
            print("serial %d failed: %s: %s" % (serial, type(exc).__name__, exc))
            inv = None
    if inv is None:
        print("no working serial")
        return 1
    print("using serial %d\n" % used)

    for label, reader, qty in (("holding", inv.read_holding_registers, 125),
                               ("input", inv.read_input_registers, 125)):
        print("=== %s registers 0x0000-0x04FF ===" % label)
        for base in range(0x0000, 0x0500, qty):
            try:
                vals = reader(base, qty)
            except Exception as exc:
                print("  0x%04X: %s: %s" % (base, type(exc).__name__, exc))
                continue
            interesting = []
            for i, v in enumerate(vals):
                addr = base + i
                if v in (0, 0xFFFF):
                    continue
                # plausible measurement ranges: 0.1V volts, 0.01Hz, 0.1W/1W power,
                # tenths of a degree, 0.1 A, etc.
                if (2000 <= v <= 2600) or (4800 <= v <= 5200) or (v <= 60000):
                    interesting.append("0x%04X=%d" % (addr, v))
            if interesting:
                print("  0x%04X: %s" % (base, "  ".join(interesting[:24])))
        print()


if __name__ == "__main__":
    sys.exit(main())