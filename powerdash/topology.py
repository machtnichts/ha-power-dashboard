#!/usr/bin/env python3
"""Topology evidence: find every PV/meter-ish entity in HA and where it lives."""
import json, os, re, urllib.request
from pathlib import Path

def load_env():
    for line in (Path.home() / ".hermes" / ".env").read_text(errors="replace").splitlines():
        line = line.strip()
        if line.startswith("HASS_") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

load_env()
req = urllib.request.Request(os.environ["HASS_URL"].rstrip("/") + "/api/states",
                            headers={"Authorization": "Bearer " + os.environ["HASS_TOKEN"]})
states = json.loads(urllib.request.urlopen(req, timeout=25).read().decode())

pat = re.compile(r"garage|sdm|solar|pv|einspeis|erzeug|inverter|wechsel|shelly|tasmota|modbus|proxy|evcharg", re.I)
print("=== entities matching garage/pv/meter-ish ===")
for s in sorted(states, key=lambda x: x["entity_id"]):
    eid = s["entity_id"]
    fn = (s.get("attributes") or {}).get("friendly_name") or ""
    if pat.search(eid) or pat.search(fn):
        unit = (s.get("attributes") or {}).get("unit_of_measurement") or ""
        print("%-62s %12s %-6s %s" % (eid, s["state"], unit, fn))

print("\n=== device-ish integrations present (by entity prefix) ===")
prefixes = {}
for s in states:
    p = s["entity_id"].split(".")[0]
    prefixes[p] = prefixes.get(p, 0) + 1
print(prefixes)