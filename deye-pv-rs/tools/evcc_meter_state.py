#!/usr/bin/env python3
"""Print the meter powers from evcc's /api/state, for a quick health check.

Usage: python3 tools/evcc_meter_state.py [state.json]
With no argument it fetches http://127.0.0.1:7070/api/state itself (urllib, no
shell pipe), so nothing is executed from the network.
"""

import json
import sys
import urllib.request


def load():
    if len(sys.argv) > 1:
        with open(sys.argv[1]) as fh:
            return json.load(fh)
    with urllib.request.urlopen("http://127.0.0.1:7070/api/state", timeout=6) as r:
        return json.loads(r.read().decode())


def main():
    s = load()
    print("database : %s" % s.get("database"))
    for key in ("grid", "pv", "battery"):
        val = s.get(key)
        if isinstance(val, list):
            for i, dev in enumerate(val):
                print("%-8s #%d power=%-8s %s" % (key, i, dev.get("power"), dev.get("title", "")))
        elif isinstance(val, dict):
            print("%-8s power=%-8s %s" % (key, val.get("power"), val.get("title", "")))
            for dev in val.get("devices", []):
                print("         device %-8s power=%-8s %s"
                      % (dev.get("name"), dev.get("power"), dev.get("title", "")))
        else:
            print("%-8s %r" % (key, val))
    for lp in s.get("loadpoints", []):
        print("loadpoint %-6s chargePower=%-6s connected=%s mode=%s"
              % (lp.get("name"), lp.get("chargePower"), lp.get("connected"), lp.get("mode")))
    # anything that looks like a meter error
    text = json.dumps(s)
    for needle in ("error", "unreachable", "timeout"):
        if needle in text.lower():
            print("NOTE: the word %r appears in the state - check for a meter error" % needle)


if __name__ == "__main__":
    sys.exit(main())