#!/usr/bin/env python3
"""Print the exact V5 frame the reference library builds, so the Rust encoder can
be checked against bytes rather than reasoning.

The library connects on construction, so a throwaway listening socket is opened
for it to connect to. Nothing is sent.
"""

import socket
import sys
import threading

sys.path.insert(0, "/home/adermake/HA-POWER-DASHBOARD/.venv/lib/python3.12/site-packages")

from pysolarmanv5 import PySolarmanV5  # noqa: E402

SERIAL = 3842831288


def crc16(data: bytes) -> bytes:
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return bytes([crc & 0xFF, (crc >> 8) & 0xFF])


def main():
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    threading.Thread(target=listener.accept, daemon=True).start()

    inv = PySolarmanV5("127.0.0.1", SERIAL, port=port, mb_slave_id=1,
                       socket_timeout=1, auto_reconnect=False)
    inv.sequence_number = 0x2A

    pdu = bytes([1, 0x03]) + (0).to_bytes(2, "big") + (125).to_bytes(2, "big")
    rtu = pdu + crc16(pdu)
    frame = inv._v5_frame_encoder(rtu)

    print("rtu            : %s (%d bytes)" % (rtu.hex(), len(rtu)))
    print("frame          : %s" % frame.hex())
    print("frame length   : %d" % len(frame))
    print("length field   : %s" % int.from_bytes(frame[1:3], "little"))
    print("control        : %04x" % int.from_bytes(frame[3:5], "little"))
    print("sequence byte5 : %02x" % frame[5])
    print("serial 7..11   : %s" % bytes(frame[7:11]).hex())
    print("byte 11        : %02x" % frame[11])
    print("paylod len +13 : %d" % (13 + int.from_bytes(frame[1:3], "little")))
    print("rtu starts at  : %d" % frame.find(rtu))
    print("bytes before rtu: %s" % bytes(frame[:frame.find(rtu)]).hex())
    print("checksum byte  : %02x (sum of 1..-2 = %02x)"
          % (frame[-2], sum(frame[1:-2]) & 0xFF))
    print("last byte      : %02x" % frame[-1])

    # and what the library's own decoder makes of it
    try:
        decoded = inv._v5_frame_decoder(bytes(frame))
        print("lib decoder    : %s  (== rtu? %s)" % (decoded.hex(), decoded == rtu))
    except Exception as exc:
        print("lib decoder    : refused (%s: %s)" % (type(exc).__name__, exc))


if __name__ == "__main__":
    sys.exit(main())
