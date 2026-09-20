#!/usr/bin/env python3
"""Discovery: list every power (W) and energy (kWh) entity in Home Assistant.

Reads HASS_URL / HASS_TOKEN from the environment, or from ~/.hermes/.env as a
fallback so the tool works from a plain cron/systemd context too.
Stdlib only - no scheduler, no HTTP framework, no requests.
"""

import json
import os
import re
import sys
import urllib.request
from pathlib import Path

ENV_FILE = Path.home() / ".hermes" / ".env"


def _load_env_fallback():
    """Populate HASS_* from ~/.hermes/.env when the process env lacks them."""
    if os.environ.get("HASS_URL") and os.environ.get("HASS_TOKEN"):
        return
    if not ENV_FILE.exists():
        return
    for line in ENV_FILE.read_text(errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip().strip('"').strip("'")
        if key.startswith("HASS_") and not os.environ.get(key):
            os.environ[key] = val


def api(path):
    _load_env_fallback()
    url = os.environ["HASS_URL"].rstrip("/") + path
    req = urllib.request.Request(url, headers={
        "Authorization": "Bearer " + os.environ["HASS_TOKEN"],
        "Content-Type": "application/json",
    })
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode())


def main():
    states = api("/api/states")
    print("total entities:", len(states))

    power, energy = [], []
    for st in states:
        eid = st["entity_id"]
        attrs = st.get("attributes") or {}
        unit = (attrs.get("unit_of_measurement") or "").strip()
        dc = attrs.get("device_class")
        sc = attrs.get("state_class")
        if unit in ("W", "kW", "kW/h") or dc == "power":
            power.append((eid, st["state"], unit, dc, sc, attrs.get("friendly_name")))
        elif unit in ("kWh", "Wh", "MWh") or dc == "energy":
            energy.append((eid, st["state"], unit, dc, sc, attrs.get("friendly_name")))

    def dump(title, rows):
        print("\n=== %s (%d) ===" % (title, len(rows)))
        for eid, state, unit, dc, sc, fn in sorted(rows):
            print("%-72s %14s %-5s %-8s %-12s %s" % (eid, state, unit, dc or "-", sc or "-", fn or ""))

    dump("POWER (W)", power)
    dump("ENERGY (kWh)", energy)

    # anything whose name hints at consumption but was not caught by unit/class
    print("\n=== name-hint stragglers not caught above ===")
    caught = {r[0] for r in power} | {r[0] for r in energy}
    for st in states:
        eid = st["entity_id"]
        if eid in caught:
            continue
        if re.search(r"leistung|verbrauch|energie|power|energy|summe", eid + " " + str((st.get("attributes") or {}).get("friendly_name", "")), re.I):
            attrs = st.get("attributes") or {}
            print("%-72s %14s %-6s %s" % (eid, st["state"], attrs.get("unit_of_measurement") or "-", attrs.get("friendly_name") or ""))


if __name__ == "__main__":
    sys.exit(main())