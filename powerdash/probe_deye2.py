#!/usr/bin/env python3
"""Characterise 192.168.178.33: MAC/vendor, unauthenticated HTTP endpoints,
and whether the 8899 service speaks SolarMAN V5 (Deye/Sofar/Growatt logger).
"""
import json
import re
import socket
import subprocess
import sys
import urllib.error
import urllib.request

HOST = "192.168.178.33"
PATHS = ["/", "/status", "/index.html", "/login.html", "/api", "/api/status",
         "/api/v1/status", "/cgi-bin/", "/config", "/param.cgi", "/device/info",
         "/solar", "/solar/api", "/status.html", "/cgi/status.cgi"]


def arp_mac(ip):
    try:
        txt = subprocess.run(["ip", "neigh", "show", ip], capture_output=True,
                             text=True, timeout=10).stdout
        m = re.search(r"lladdr ([0-9a-f:]{17})", txt)
        return m.group(1) if m else None
    except Exception:
        return None


def touch(port):
    s = socket.socket()
    s.settimeout(2)
    try:
        s.connect((HOST, port))
    except Exception:
        pass
    finally:
        s.close()


def http(path, timeout=5):
    url = "http://%s%s" % (HOST, path)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.headers.get("Server"), r.read(600).decode(errors="replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.headers.get("Server"), exc.read(300).decode(errors="replace")
    except Exception as exc:
        return None, None, str(exc)


def solarman_probe():
    """Send a SolarMAN V5 read-holding-registers frame and show the raw reply.

    Frame layout (SolarMAN V5, as implemented by pysolarmanv5):
      0xA5 | len(2, LE) | 0x10 | seq(2) | 0x0000 | 0x02 | 0x0000 |
      work_time(4) | power_on(4) | offset(4) | 0 (4)  ... hmm
    Rather than guess the header, this sends the widely used minimal request and
    prints whatever comes back so the framing can be checked against a real reply.
    """
    frame = bytes([
        0xA5, 0x17, 0x00, 0x10, 0x45, 0x00, 0x00, 0x00, 0x02, 0x00, 0x00, 0x00,
        0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
        0x01, 0x03, 0x00, 0x00, 0x00, 0x02,
        0x00, 0x00,
    ])
    s = socket.socket()
    s.settimeout(6)
    try:
        s.connect((HOST, 8899))
        s.sendall(frame)
        data = s.recv(1024)
        return data
    except Exception as exc:
        return "ERROR: %s" % exc
    finally:
        s.close()


def main():
    touch(80)
    touch(8899)
    mac = arp_mac(HOST)
    print("MAC of %s: %s" % (HOST, mac))
    if mac:
        oui = mac.replace(":", "")[:6].upper()
        print("OUI prefix: %s  (look this up to confirm the vendor)" % oui)

    print("\n=== HTTP endpoints (no auth) ===")
    for p in PATHS:
        code, server, body = http(p)
        snippet = " ".join(body.split())[:110]
        print("  %-18s -> %-6s server=%-10s %s" % (p, code, server or "-", snippet))

    print("\n=== SolarMAN V5 on 8899 ===")
    reply = solarman_probe()
    if isinstance(reply, bytes):
        print("  raw reply (%d bytes): %s" % (len(reply), reply.hex(" ")))
        print("  first byte 0x%02X (0xA5 = valid SolarMAN frame)" % reply[0] if reply else "  empty")
    else:
        print(" ", reply)


if __name__ == "__main__":
    sys.exit(main())