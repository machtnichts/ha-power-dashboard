#!/usr/bin/env python3
"""Which response layout does the real logger use?

The reference library builds requests with the Modbus RTU frame at offset 26, but
its *decoder* slices responses at offset 25. One of the two must be right for this
device - and since the library reads this logger in production (correct AC power,
energy counter ticking), whichever layout its decoder actually turns into correct
values is the layout the device sends.

This builds a response frame both ways, runs the library's own decoder plus the
Modbus parser it uses internally, and prints what each produces.
"""

import socket
import sys
import threading

sys.path.insert(0, "/home/adermake/HA-POWER-DASHBOARD/.venv/lib/python3.12/site-packages")

from pysolarmanv5 import PySolarmanV5  # noqa: E402
from pysolarmanv5.pysolarmanv5 import rtu  # noqa: E402

SERIAL = 3842831288
SEQUENCE = 0x2A


def crc16(data: bytes) -> bytes:
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return bytes([crc & 0xFF, (crc >> 8) & 0xFF])


def build_response(rtu_frame: bytes, prefix_len: int) -> bytes:
    """A 0x1510 response with the RTU frame starting at `prefix_len`."""
    # byte 11 is the frame type (0x02); everything else in the prefix is zero
    filler = b"\x02" + bytes(prefix_len - 12)
    frame = bytearray()
    frame.append(0xA5)
    frame += (0).to_bytes(2, "little")  # length placeholder
    frame += (0x1510).to_bytes(2, "little")
    frame.append(SEQUENCE)
    frame.append(0x00)
    frame += SERIAL.to_bytes(4, "little")
    frame += filler
    assert len(frame) == prefix_len, (len(frame), prefix_len)
    frame += rtu_frame
    frame += b"\x00\x15"  # checksum placeholder + end
    frame[1:3] = (len(frame) - 13).to_bytes(2, "little")  # 13 + length == frame len
    frame[-2] = sum(frame[1:-2]) & 0xFF
    return bytes(frame)


def main():
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    threading.Thread(target=listener.accept, daemon=True).start()

    inv = PySolarmanV5("127.0.0.1", SERIAL, port=port, mb_slave_id=1,
                       socket_timeout=1, auto_reconnect=False)
    inv.sequence_number = SEQUENCE

    # a plausible FC3 response: 4 registers, values 0x000A, 0x0014, 0x001E, 0x0028
    body = bytes([1, 0x03, 8]) + (10).to_bytes(2, "big") + (20).to_bytes(2, "big") \
        + (30).to_bytes(2, "big") + (40).to_bytes(2, "big")
    rtu_frame = body + crc16(body)
    print("expected rtu   : %s" % rtu_frame.hex())
    expected_values = [10, 20, 30, 40]
    print("expected values: %s" % expected_values)
    print()

    request = bytes([1, 0x03]) + (0).to_bytes(2, "big") + (4).to_bytes(2, "big")
    request += crc16(request)

    for prefix_len in (25, 26):
        frame = build_response(rtu_frame, prefix_len)
        print("--- response with the RTU at offset %d ---" % prefix_len)
        print("frame          : %s" % frame.hex())
        print("length field   : %d (frame len %d)" % (int.from_bytes(frame[1:3], "little"), len(frame)))
        try:
            decoded = inv._v5_frame_decoder(bytes(frame))
            print("decoder returns: %s (%d bytes)" % (decoded.hex(), len(decoded)))
        except Exception as exc:
            print("decoder        : refused (%s: %s)" % (type(exc).__name__, exc))
            print()
            continue
        try:
            values = rtu.parse_response_adu(decoded, request)
            print("parsed values  : %s   %s" % (values, "CORRECT" if list(values) == expected_values else "WRONG"))
        except Exception as exc:
            print("parser         : failed (%s: %s)" % (type(exc).__name__, exc))
        print()


if __name__ == "__main__":
    sys.exit(main())
