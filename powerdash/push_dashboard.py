#!/usr/bin/env python3
"""Push docs/dashboard.json into Home Assistant as a storage-mode Lovelace board.

Idempotent: creates the dashboard if it is absent, then saves the config, then
reads it back and compares. Writes only to url_path 'strom' - it never touches
the existing dashboards (meine-energie, dashboard-klima, ...).

Usage:  python3 -m powerdash.push_dashboard [--dry-run]
"""

import json
import sys
from pathlib import Path

from powerdash.ha_ws import HAWebSocket

CFG = Path(__file__).resolve().parent.parent / "docs" / "dashboard.json"
URL_PATH = "strom-verbrauch"


def main():
    dry = "--dry-run" in sys.argv
    cfg = json.loads(CFG.read_text())
    print("config: %s (%d views)" % (CFG, len(cfg["views"])))

    with HAWebSocket() as ha:
        boards = ha.call("lovelace/dashboards/list")
        existing = {b["url_path"]: b for b in boards}

        if URL_PATH in existing:
            board = existing[URL_PATH]
            print("dashboard '%s' already exists -> updating it (mode=%s)"
                  % (URL_PATH, board.get("mode")))
            if board.get("mode") != "storage":
                print("!! mode is %r, not 'storage' - refusing to overwrite"
                      % board.get("mode"))
                return 1
        elif dry:
            print("[dry-run] would create dashboard '%s'" % URL_PATH)
        else:
            board = ha.call("lovelace/dashboards/create",
                            url_path=URL_PATH,
                            title="Strom",
                            icon="mdi:flash",
                            show_in_sidebar=True,
                            require_admin=False,
                            mode="storage")
            print("created dashboard: %s" % json.dumps(board, ensure_ascii=False))

        if dry:
            print("[dry-run] would save %d views" % len(cfg["views"]))
            return 0

        ha.call("lovelace/config/save", url_path=URL_PATH, config=cfg)
        print("saved config")

        back = ha.call("lovelace/config", url_path=URL_PATH)
        if back == cfg:
            print("READ-BACK: identical to what was sent (%d views, %d entities)"
                  % (len(back["views"]), count_entities(back)))
            return 0
        print("!! READ-BACK DIFFERS")
        print("sent views: %d, got views: %d" % (len(cfg["views"]), len(back.get("views", []))))
        print(json.dumps(back, ensure_ascii=False)[:800])
        return 1


def count_entities(node):
    n = 0
    if isinstance(node, dict):
        if isinstance(node.get("entity"), str):
            n += 1
        for v in node.values():
            n += count_entities(v)
    elif isinstance(node, list):
        for v in node:
            n += count_entities(v)
    return n


if __name__ == "__main__":
    sys.exit(main())