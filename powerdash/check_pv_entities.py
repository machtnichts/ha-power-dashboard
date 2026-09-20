#!/usr/bin/env python3
"""Confirm the MQTT-discovered garage PV entities exist in HA and carry values."""
import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from powerdash.env import ha_token, ha_url

WANT = ["garage_pv"]


def main():
    req = urllib.request.Request(ha_url() + "/api/states",
                                 headers={"Authorization": "Bearer " + ha_token()})
    with urllib.request.urlopen(req, timeout=20) as r:
        states = json.loads(r.read().decode())

    hits = [s for s in states if any(w in s["entity_id"] for w in WANT)]
    print("garage PV entities found: %d" % len(hits))
    for s in sorted(hits, key=lambda x: x["entity_id"]):
        a = s.get("attributes") or {}
        print("  %-34s %-12s %-6s %s"
              % (s["entity_id"], s["state"], a.get("unit_of_measurement") or "",
                 a.get("friendly_name") or ""))
    return 0 if hits else 1


if __name__ == "__main__":
    sys.exit(main())