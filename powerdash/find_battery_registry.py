#!/usr/bin/env python3
"""Look for battery charge/discharge entities in the REGISTRY, including disabled
ones (disabled entities never appear in /api/states, so a state-only search can
miss exactly the sensor you are looking for)."""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from powerdash.ha_ws import HAWebSocket

PAT = re.compile(r"batter|charge|discharg|entlad", re.I)


def main():
    with HAWebSocket() as ha:
        reg = ha.call("config/entity_registry/list")
        mine = [e for e in reg
                if PAT.search(e.get("entity_id", "") + " " + str(e.get("original_name") or ""))
                and str(e.get("platform") or "") in ("evcc_intg", "mqtt", "solaredge", "modbus")]
        print("registry entries (battery-ish, evcc/mqtt/solaredge platform): %d" % len(mine))
        for e in sorted(mine, key=lambda x: x["entity_id"]):
            print("  %-50s platform=%-12s disabled_by=%-10s name=%s"
                  % (e["entity_id"], e.get("platform"), e.get("disabled_by") or "-",
                     e.get("original_name") or "-"))

        # everything the evcc integration owns, disabled or not
        evcc = [e for e in reg if str(e.get("platform")) == "evcc_intg"]
        print("\nevcc_intg registry entries total: %d (disabled: %d)"
              % (len(evcc), sum(1 for e in evcc if e.get("disabled_by"))))
        print("disabled evcc entities:")
        for e in sorted(evcc, key=lambda x: x["entity_id"]):
            if e.get("disabled_by"):
                print("  %-58s %s" % (e["entity_id"], e["disabled_by"]))

        # and every device, to see whether a separate battery device exists
        devs = ha.call("config/device_registry/list")
        bat = [d for d in devs
               if PAT.search((d.get("name") or "") + " " + str(d.get("manufacturer") or ""))]
        print("\ndevices matching battery-ish: %d" % len(bat))
        for d in bat:
            print("  %-40s %-16s %s" % (d.get("name"), d.get("model"), d.get("manufacturer")))


if __name__ == "__main__":
    main()