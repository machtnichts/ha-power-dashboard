#!/usr/bin/env python3
"""Print the rendered tables exactly as HA will hand them to the markdown card."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from powerdash.verify_render import render_rest

CFG = Path(__file__).resolve().parent.parent / "docs" / "dashboard.json"


def walk(node, out):
    if isinstance(node, dict):
        if node.get("type") == "markdown" and ("content" in node or "text" in node):
            out.append(node.get("content", node.get("text")))
        for v in node.values():
            walk(v, out)
    elif isinstance(node, list):
        for v in node:
            walk(v, out)


def main():
    cfg = json.loads(CFG.read_text())
    tmpls = []
    walk(cfg, tmpls)
    for t in tmpls:
        if t.strip().startswith("<small>"):
            continue
        out, err = render_rest(t)
        print("=" * 78)
        print(err or out)
    return 0


if __name__ == "__main__":
    sys.exit(main())