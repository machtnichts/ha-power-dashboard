#!/usr/bin/env python3
"""Render the dashboard's markdown cards to a local HTML preview and screenshot it.

The card bodies are rendered by HA itself (REST /api/template), so the text is
exactly what the markdown card will receive - only the wrapper is local. This is a
layout check that needs no Home Assistant login.
"""
import html
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from powerdash.verify_render import render_rest

HERE = Path(__file__).resolve().parent.parent
CFG = HERE / "docs" / "dashboard.json"
OUT = HERE / "docs" / "preview.html"


def md_to_html(md):
    """Minimal converter for what these cards actually contain: headings, bold,
    small, tables, <b> tags."""
    out, i = [], 0
    lines = md.split("\n")
    while i < len(lines):
        ln = lines[i]
        if ln.startswith("### "):
            out.append("<h3>%s</h3>" % html.escape(ln[4:]))
            i += 1
        elif ln.startswith("|"):
            rows = []
            while i < len(lines) and lines[i].startswith("|"):
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                rows.append(cells)
                i += 1
            if len(rows) >= 2 and set(rows[1][0]) <= set(":- "):
                header, body = rows[0], rows[2:]
                align = []
                for c in rows[1]:
                    align.append("right" if c.rstrip().endswith(":") else "left")
            else:
                header, body, align = None, rows, []
            t = ['<table class="tbl">']
            if header is not None:
                t.append("<thead><tr>")
                for n, c in enumerate(header):
                    a = align[n] if n < len(align) else "left"
                    t.append('<th style="text-align:%s">%s</th>' % (a, inline(c)))
                t.append("</tr></thead>")
            t.append("<tbody>")
            for r in body:
                t.append("<tr>")
                for n, c in enumerate(r):
                    a = align[n] if n < len(align) else "left"
                    t.append('<td style="text-align:%s">%s</td>' % (a, inline(c)))
                t.append("</tr>")
            t.append("</tbody></table>")
            out.append("".join(t))
        elif ln.strip().startswith("<small>"):
            out.append('<p class="note">%s</p>' % ln.strip())
            i += 1
        elif ln.strip():
            out.append("<p>%s</p>" % inline(ln.strip()))
            i += 1
        else:
            i += 1
    return "\n".join(out)


def inline(text):
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    return text


def collect(node, out):
    if isinstance(node, dict):
        if node.get("type") == "heading":
            out.append(("h", node.get("heading", "")))
        elif node.get("type") == "markdown" and "content" in node:
            out.append(("md", node["content"]))
        for v in node.values():
            collect(v, out)
    elif isinstance(node, list):
        for v in node:
            collect(v, out)


def main():
    cfg = json.loads(CFG.read_text())
    parts = ["<html><head><meta charset='utf-8'><style>",
             "body{background:#111;color:#e1e1e1;font-family:Roboto,system-ui,sans-serif;",
             "margin:0;padding:24px;max-width:1180px}",
             ".viewname{color:#9aa;font-size:13px;letter-spacing:.14em;text-transform:uppercase;",
             "margin:26px 0 10px;border-top:1px solid #333;padding-top:14px}",
             "h2.section{font-size:19px;margin:20px 0 6px;color:#fff}",
             ".card{background:#1c1c1c;border-radius:12px;padding:14px 18px;margin:10px 0;",
             "box-shadow:0 1px 3px rgba(0,0,0,.4)}",
             "table.tbl{width:100%;border-collapse:collapse;font-size:14.5px}",
             ".tbl th{color:#9aa;font-weight:500;font-size:13px;border-bottom:1px solid #3a3a3a;",
             "padding:6px 10px}",
             ".tbl td{padding:6px 10px;border-bottom:1px solid #262626}",
             ".note{color:#8a8a8a;font-size:12.5px;line-height:1.5;margin:8px 0 0}",
             "</style></head><body>"]
    parts.append("<h1 style='font-size:22px'>Strom — Vorschau (Layout-Check)</h1>")
    for view in cfg.get("views", []):
        parts.append('<div class="viewname">Ansicht: %s</div>' % view.get("title", ""))
        cards = []
        if view.get("sections"):
            for s in view["sections"]:
                collect(s.get("cards", []), cards)
        else:
            collect(view.get("cards", []), cards)
        for kind, payload in cards:
            if kind == "h":
                parts.append('<h2 class="section">%s</h2>' % html.escape(payload))
            else:
                rendered, err = render_rest(payload)
                body = md_to_html(rendered) if not err else \
                    "<pre style='color:#f66'>%s</pre>" % html.escape(str(err))
                parts.append('<div class="card">%s</div>' % body)
    parts.append("</body></html>")
    OUT.write_text("\n".join(parts))
    print("wrote %s (%d bytes)" % (OUT, OUT.stat().st_size))
    return 0


if __name__ == "__main__":
    sys.exit(main())