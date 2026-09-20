#!/usr/bin/env python3
"""Is the native Solarman integration available in this HA, and can we see the
config-flow it would need? Read-only: lists manifests, starts no flows."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from powerdash.ha_ws import HAWebSocket


def main():
    with HAWebSocket() as ha:
        # new integrations domain: "manifest/list" (list of manifests)
        got = None
        for cmd in ("manifest/list", "manifest/get"):
            try:
                if cmd == "manifest/get":
                    got = ha.call(cmd, integration="solarman")
                else:
                    got = ha.call(cmd)
                print("== %s -> ok" % cmd)
                break
            except Exception as exc:
                print("== %s -> %s" % (cmd, str(exc)[:120]))

        if isinstance(got, dict):
            interesting = {k: v for k, v in got.items()
                           if any(w in k.lower() for w in ("solarman", "deye", "solar", "modbus"))}
            print(json.dumps(interesting, ensure_ascii=False, indent=2)[:1500])
        elif isinstance(got, list):
            names = [m.get("domain") for m in got if isinstance(m, dict)]
            hits = [n for n in names if n and any(
                w in n.lower() for w in ("solarman", "deye", "modbus", "solar"))]
            print("total manifests: %d" % len(names))
            print("matching:", sorted(hits))
        else:
            print("unexpected reply:", str(got)[:300])

        # current config entries, so we can see what is installed already
        try:
            entries = ha.call("config_entries/get")
            print("\n%d config entries" % len(entries))
            for e in entries:
                print("  %-28s %-22s %s" % (e.get("domain"), e.get("title"),
                                            e.get("state")))
        except Exception as exc:
            print("config_entries/get failed:", exc)


if __name__ == "__main__":
    main()