#!/usr/bin/env python3
"""Minimal SolarMAN V5 client, written from pysolarmanv5's frame layout, that
prints raw replies.

Layout (request):  A5 | len(2,LE) | 0x4510 | seq(2,LE) | loggerserial(4,LE) |
                   frametype(1)=02 | sensortype(2) | delivery(4) | poweron(4) |
                   offset(4) | modbus-RTU | checksum(1) | 0x15
Reply:             A5 | len(2,LE) | 0x1510 | seq(1..2) | loggerserial(4,LE) | ...
                   modbus-RTU | checksum(1) | 0x15
len = 15 + len(modbus); total frame = 13 + len

The point of this script is to find out what the device actually puts in the
reply's serial field, instead of trusting a library's validation.
"""

import socket
import struct
import sys

HOST = "192.168.178.33"
PORT = 8899


def crc16(data: bytes) -> bytes:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return struct.pack("<H", crc)


def build(serial: int, seq: int, mb: bytes) -> bytes:
    length = struct.pack("<H", 15 + len(mb))
    control = struct.pack("<H", 0x4510)
    seq_b = struct.pack("<H", seq)
    logserial = struct.pack("<I", serial)
    payload = (bytes.fromhex("02") + bytes.fromhex("0000") +
               bytes.fromhex("00000000") * 3 + mb)
    frame = bytearray(b"\xa5" + length + control + seq_b + logserial + payload)
    checksum = sum(frame[1:]) & 0xFF
    frame += bytes([checksum, 0x15])
    return bytes(frame)


def talk(frame: bytes, timeout: float = 8.0) -> bytes:
    s = socket.socket()
    s.settimeout(timeout)
    try:
        s.connect((HOST, PORT))
        s.sendall(frame)
        return s.recv(1024)
    finally:
        s.close()


def decode(reply: bytes):
    if len(reply) < 13:
        return {"error": "short reply"}
    payload_len = struct.unpack("<H", reply[1:3])[0]
    return {
        "len": len(reply),
        "length_field": payload_len,
        "expected_total": 13 + payload_len,
        "control": reply[3:5].hex(" "),
        "seq_b5": reply[5],
        "serial_7_11_le": struct.unpack("<I", reply[7:11])[0],
        "serial_7_11_hex": reply[7:11].hex(" "),
        "payload_from_25": reply[25:-2].hex(" "),
        "modbus_guess_from_26": reply[26:-2].hex(" "),
    }


def main():
    mb = b"\x01\x03\x00\x00\x00\x02"
    mb += crc16(mb)

    for serial in (3841202104, 0, 512, 1, 0xB8F30CE5):
        frame = build(serial, 0x01, mb)
        try:
            reply = talk(frame)
        except Exception as exc:
            print("serial %-12d -> %s: %s" % (serial, type(exc).__name__, exc))
            continue
        info = decode(reply)
        print("serial %-12d sent=%s" % (serial, frame.hex(" ")))
        print("                  reply=%s" % reply.hex(" "))
        print("                  serial_in_reply=%s (%d) payload@25=%s"
              % (info["serial_7_11_hex"], info["serial_7_11_le"],
                 info["payload_from_25"]))
        print()


if __name__ == "__main__":
    sys.exit(main())