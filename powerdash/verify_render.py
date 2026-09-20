#!/usr/bin/env python3
"""Verify the generated dashboard BEFORE pushing it.

1. Every entity referenced in docs/dashboard.json exists in HA (or is reported as
   missing) - a typo'd entity renders as an error card, so catch it here.
2. EVERY markdown template is rendered through HA's own Jinja engine, so a broken
   expression is caught now instead of showing as an error card in the UI.
3. The rendered plug table is cross-checked against a sum computed independently
   in Python from /api/states.
"""

import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from powerdash.ha_ws import load_env

CFG = Path(__file__).resolve().parent.parent / "docs" / "dashboard.json"


def render_rest(template):
    """Render through HA's REST template endpoint (ws render_template returns null
    on HA 2026.9; REST /api/template is the documented path and returns the text)."""
    load_env()
    url = os.environ["HASS_URL"].rstrip("/") + "/api/template"
    body = json.dumps({"template": template}).encode()
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Authorization": "Bearer " + os.environ["HASS_TOKEN"],
        "Content-Type": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            return resp.read().decode(), None
    except urllib.error.HTTPError as exc:
        return None, "HTTP %s: %s" % (exc.code, exc.read().decode(errors="replace"))


def rest_states():
    load_env()
    url = os.environ["HASS_URL"].rstrip("/")
    req = urllib.request.Request(url + "/api/states",
                                 headers={"Authorization": "Bearer " + os.environ["HASS_TOKEN"]})
    with urllib.request.urlopen(req, timeout=25) as resp:
        return json.loads(resp.read().decode())


def walk(node, entities, templates, bad=None):
    if bad is None:
        bad = []
    if isinstance(node, dict):
        if isinstance(node.get("entity"), str):
            entities.add(node["entity"])
        for key in ("entities", "badges"):
            for e in node.get(key, []) or []:
                if isinstance(e, str):
                    entities.add(e)
                elif isinstance(e, dict) and isinstance(e.get("entity"), str):
                    entities.add(e["entity"])
        if node.get("type") == "markdown":
            if "content" in node:
                templates.append(node["content"])
            else:
                bad.append("markdown card without 'content' (got keys %s)"
                           % sorted(node.keys()))
        for v in node.values():
            walk(v, entities, templates, bad)
    elif isinstance(node, list):
        for v in node:
            walk(v, entities, templates, bad)


def main():
    cfg = json.loads(CFG.read_text())
    entities, templates, bad = set(), [], []
    walk(cfg, entities, templates, bad)

    # schema sanity on card keys - a wrong key renders as "Konfigurationsfehler"
    # in the UI rather than as an error anywhere we could see it.
    # Views, sections and cards are checked separately: a section ALSO uses
    # type: grid and legitimately has column_span, so shape alone is ambiguous.
    card_keys = {
        "heading": {"type", "heading", "heading_style", "icon", "badge", "tap_action",
                    "visibility"},
        "markdown": {"type", "content", "title", "card_size", "entity_id", "theme",
                     "show_empty", "text_only", "tap_action", "visibility"},
        "entities": {"type", "entities", "title", "state_color", "show_header_toggle",
                     "theme", "visibility"},
        "grid": {"type", "cards", "columns", "square", "title", "visibility"},
        "statistics-graph": {"type", "entities", "title", "hours_to_show", "chart_type",
                             "stat_types", "days_to_show", "period", "visibility"},
    }
    section_keys = {"type", "column_span", "row_span", "cards", "background",
                    "visibility", "heading", "title"}
    view_keys = {"title", "path", "icon", "badges", "type", "max_columns", "sections",
                 "cards", "subview", "back_path", "theme", "visible",
                 "dense_section_placement", "layout"}

    def check_card(card):
        if not isinstance(card, dict):
            return
        t = card.get("type")
        if t in card_keys:
            unknown = set(card.keys()) - card_keys[t]
            if unknown:
                bad.append("%s card has unknown key(s) %s" % (t, sorted(unknown)))
        if isinstance(card.get("cards"), list):
            for c in card["cards"]:
                check_card(c)

    def check_section(sec):
        if not isinstance(sec, dict):
            return
        unknown = set(sec.keys()) - section_keys
        if unknown:
            bad.append("section has unknown key(s) %s" % sorted(unknown))
        for c in sec.get("cards", []) or []:
            check_card(c)

    def check_view(view):
        if not isinstance(view, dict):
            return
        unknown = set(view.keys()) - view_keys
        if unknown:
            bad.append("view %r has unknown key(s) %s" % (view.get("path"), sorted(unknown)))
        if isinstance(view.get("sections"), list):
            for s in view["sections"]:
                check_section(s)
        elif isinstance(view.get("cards"), list):
            for c in view["cards"]:
                check_card(c)

    for view in cfg.get("views", []):
        check_view(view)

    if bad:
        print("SCHEMA PROBLEMS:")
        for b in bad:
            print("  -", b)
    else:
        print("card schema: no unknown keys in view/section/markdown/heading/card objects")

    states = rest_states()
    known = {s["entity_id"] for s in states}
    missing = sorted(e for e in entities if e not in known)
    print("entities referenced: %d   missing: %d" % (len(entities), len(missing)))
    for m in missing:
        print("  MISSING:", m)

    print("\nrendering %d markdown templates through HA:" % len(templates))
    rendered_all = []
    fails = 0
    for i, tpl in enumerate(templates):
        out, err = render_rest(tpl)
        head = " ".join(tpl.split())[:70]
        if err:
            fails += 1
            print("  [%d] FAIL  %s ...\n      %s" % (i, head, err.strip()[:500]))
            rendered_all.append(None)
        else:
            print("  [%d] ok    %s ..." % (i, head))
            rendered_all.append(out)

    ok = not missing and fails == 0 and not bad

    # --- cross-check the plug totals against an independent python sum ---
    plug_tpl = next((t for t in templates if "Σ Summe" in t), None)
    if plug_tpl:
        by_id = {s["entity_id"]: s for s in states}
        pairs = re.findall(r"\('([^']+)', '([^']+)', '([^']+)'\)", plug_tpl)
        tw = te = 0.0
        for _n, pw, en in pairs:
            for acc, key in ((0, pw), (1, en)):
                try:
                    v = float(by_id[key]["state"])
                except Exception:
                    continue
                if acc == 0:
                    tw += v
                else:
                    te += v
        rendered = next((r for r in rendered_all if r and "Σ Summe" in r), None)
        m = re.search(r"\*\*Σ Summe\*\* \| \*\*([\d.]+) W\*\* \| \*\*([\d.]+) kWh\*\*",
                      rendered or "")
        print("\n---- plug totals: rendered vs python ----")
        print("python   : %.1f W / %.1f kWh  (%d plugs)" % (tw, te, len(pairs)))
        if not m:
            print("!! could not parse the rendered totals row")
            ok = False
        else:
            print("rendered : %s W / %s kWh" % (m.group(1), m.group(2)))
            if abs(float(m.group(1)) - tw) > 1.5:
                print("!! W total disagrees")
                ok = False
            if abs(float(m.group(2)) - te) > 0.2:
                print("!! kWh total disagrees")
                ok = False

    # --- cross-check the house figure ---
    house_tpl = next((t for t in templates if "Hausverbrauch**" in t), None)
    if house_tpl:
        rendered = next((r for r in rendered_all if r and "Hausverbrauch**" in r), None)
        m = re.search(r"Hausverbrauch\*\*[^|]*\|\s*\*\*(-?[\d.]+) W\*\*", rendered or "")
        py = float({s["entity_id"]: s for s in states}["sensor.evcc_home_power"]["state"])
        print("\n---- house power: rendered vs python ----")
        print("python   : %.1f W" % py)
        if not m:
            print("!! could not parse the rendered house value")
            ok = False
        else:
            print("rendered : %s W" % m.group(1))
            if abs(float(m.group(1)) - py) > 1.5:
                print("!! house power disagrees")
                ok = False

    print("\nRESULT:", "OK" if ok else "PROBLEMS FOUND")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())