#!/usr/bin/env python3
"""Rename the garage PV entities to clean entity_ids.

Re-publishing discovery with object_id does NOT change an existing entity's
entity_id: HA matches on unique_id and reattaches the existing registry entry.
The entity_id is registry state, so it has to be changed in the registry.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from powerdash.deye_pv import ENTITIES
from powerdash.ha_ws import HAWebSocket

WANT = {"powerdash_%s" % topic_id: "%s.%s" % (component, object_id)
        for component, topic_id, object_id, _extra in ENTITIES}


def main():
    with HAWebSocket() as ha:
        entries = ha.call("config/entity_registry/list")
        mine = [e for e in entries if e.get("unique_id") in WANT]
        print("registry entries owned by powerdash: %d" % len(mine))
        for e in mine:
            old = e["entity_id"]
            new = WANT[e["unique_id"]]
            if old == new:
                print("  %-58s already correct" % old)
                continue
            ha.call("config/entity_registry/update", entity_id=old, new_entity_id=new)
            print("  %-58s -> %s" % (old, new))

        entries = ha.call("config/entity_registry/list")
        after = {e["entity_id"] for e in entries if e.get("unique_id") in WANT}
        print("\nfinal entity ids: %s" % sorted(after))
    return 0


if __name__ == "__main__":
    sys.exit(main())