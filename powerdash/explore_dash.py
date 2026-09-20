#!/usr/bin/env python3
"""Read-only probe: which frontend resources exist, and how does an existing
dashboard look (so the new one matches the house style)."""
import json

from powerdash.ha_ws import HAWebSocket


def main():
    with HAWebSocket() as ha:
        for cmd in ("lovelace/resources/list", "lovelace/resources"):
            print("==", cmd, "==")
            try:
                print(json.dumps(ha.call(cmd), ensure_ascii=False)[:800])
            except Exception as exc:
                print("  ->", exc)

        cfg = ha.call("lovelace/config", url_path="meine-energie")
        print("\n== meine-energie: %d views" % len(cfg.get("views", [])))
        views = cfg.get("views", [])
        if views:
            v = views[0]
            print("view keys:", list(v))
            print("view type:", v.get("type"), "| title:", v.get("title"))
            cards = v.get("sections") or v.get("cards") or []
            print("top-level card count:", len(cards))
            for c in cards[:6]:
                if isinstance(c, dict):
                    print("  card type:", c.get("type"), "| keys:", list(c)[:8])
        print("\n---- first 2500 chars of the config ----")
        print(json.dumps(cfg, ensure_ascii=False, indent=1)[:2500])


if __name__ == "__main__":
    main()