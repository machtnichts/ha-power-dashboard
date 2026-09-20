#!/usr/bin/env python3
"""Probe a LAN address for the garage PV (Deye SUN-M160G4 microinverter).

Read-only discovery: ARP entry, open TCP ports, and the HTTP banner of anything
that answers. Does not write to the device.
"""
import json
import re
import socket
import subprocess
import sys
import urllib.error
import urllib.request

TARGETS = ["192.168.178.33"]
PORTS = [
    (80, "http"), (443, "https"), (502, "modbus"), (8899, "solarman/logger"),
    (8000, "http-alt"), (8080, "http-alt"), (48899, "at/wifi-config"),
    (9999, "logger"), (5000, "http-alt"), (8888, "http-alt"), (23, "telnet"),
]


def arp_table():
    out = {}
    try:
        txt = subprocess.run(["ip", "neigh"], capture_output=True, text=True, timeout=10).stdout
        for line in txt.splitlines():
            p = line.split()
            if len(p) >= 5 and p[1] == "dev":
                out[p[0]] = {"mac": p[4], "dev": p[2], "state": p[-1]}
    except Exception as exc:
        print("ip neigh failed:", exc)
    return out


def tcp_open(host, port, timeout=1.5):
    s = socket.socket()
    s.settimeout(timeout)
    try:
        s.connect((host, port))
        return True
    except Exception:
        return False
    finally:
        s.close()


def http_probe(url, timeout=5):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "curl/8"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read(4000).decode(errors="replace")
            return {"status": r.status, "server": r.headers.get("Server"),
                    "ctype": r.headers.get("Content-Type"), "body": body[:1200]}
    except urllib.error.HTTPError as exc:
        return {"status": exc.code, "server": exc.headers.get("Server"),
                "body": exc.read(800).decode(errors="replace")}
    except Exception as exc:
        return {"error": str(exc)}


def main():
    arp = arp_table()
    print("=== ARP / neighbour table (LAN, IPv4) ===")
    v4 = {k: v for k, v in arp.items() if re.match(r"^\d+\.\d+\.\d+\.\d+$", k)}
    for ip, info in sorted(v4.items(), key=lambda kv: tuple(int(x) for x in kv[0].split("."))):
        print("  %-16s %-18s %s" % (ip, info["mac"], info["state"]))

    for host in TARGETS:
        print("\n=== %s ===" % host)
        if host in arp:
            print("  ARP: %s" % arp[host]["mac"])
        else:
            print("  ARP: no entry (device may be idle/absent); will still try to connect")

        open_ports = []
        for port, label in PORTS:
            if tcp_open(host, port):
                open_ports.append((port, label))
                print("  port %-5d OPEN   (%s)" % (port, label))
        if not open_ports:
            print("  no probed port answered")

        for port, label in open_ports:
            if port in (80, 8000, 8080, 5000, 8888):
                for scheme in ("http",):
                    url = "%s://%s:%d/" % (scheme, host, port)
                    res = http_probe(url)
                    print("  GET %s -> %s" % (url, json.dumps(res, ensure_ascii=False)[:700]))


if __name__ == "__main__":
    sys.exit(main())