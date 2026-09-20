#!/usr/bin/env python3
"""Confirm what MQTT would attach to: is a broker already listening on the HA host,
and what exactly is the current Zigbee stack?"""
import json
import socket
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from powerdash.ha_ws import HAWebSocket

HA_HOST = "192.168.178.126"


def port_open(host, port, timeout=2.0):
    s = socket.socket()
    s.settimeout(timeout)
    try:
        s.connect((host, port))
        return True
    except Exception:
        return False
    finally:
        s.close()


def main():
    for port, what in ((1883, "MQTT (plain)"), (8883, "MQTT over TLS"),
                       (1884, "MQTT alt")):
        print("%-16s %s:1883 -> %s" % (what, HA_HOST, "OPEN" if port_open(HA_HOST, port) else "closed"))

    with HAWebSocket() as ha:
        entries = ha.call("config_entries/get")
        for e in entries:
            if e.get("domain") in ("zha", "mqtt", "hassio", "template"):
                print("\nconfig entry: %-12s %-34s state=%s"
                      % (e.get("domain"), e.get("title"), e.get("state")))

        # is any MQTT-ish integration loaded at all?
        comps = ha.call("get_config")
        print("\nHA version %s | mqtt in components: %s"
              % (comps.get("version"), "mqtt" in (comps.get("components") or [])))


if __name__ == "__main__":
    main()