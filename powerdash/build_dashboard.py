#!/usr/bin/env python3
"""Build the 'Strom' Lovelace dashboard config from live Home Assistant state.

Layout is table-first: every group is a markdown card rendering a Jinja table with
numeric columns and a computed totals row, instead of tile cards.

Discovery is data-driven: a plug is any device for which HA has all three of
    <base> Leistung          (power, W)
    <base> Summe verbraucht  (energy, kWh)
    <base>                   (switch, toggleable)
matched on the *friendly name* in the entity's own state, which is what the
device actually calls itself. Nothing is hardcoded from an earlier session, so a
renamed or replaced plug is picked up (or dropped) automatically.

Writes the config to docs/dashboard.json. Does NOT push it - see push_dashboard.py.
"""

import json
import os
import re
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE.parent / "docs" / "dashboard.json"

# ---------------------------------------------------------------------------
# Topology note (see docs/README.md): evcc reads grid/pv/battery from the
# SolarEdge Hybrid inverter (db:3/4/5 = 192.168.178.84:1502) and never reads the
# SDM630. The SDM630 is a sub-meter on the garage branch = wallbox + garage PV.
# ---------------------------------------------------------------------------
HOUSE = {
    "home": "sensor.evcc_home_power",
    "grid": "sensor.evcc_grid_power",
    "pv": "sensor.evcc_pv_power",
    "battery_soc": "sensor.evcc_battery_soc",
    "battery_power": "sensor.evcc_battery_power",
    "charger_power": "sensor.evcc_go_e_charger_charge_power",
    "charger_energy": "sensor.evcc_stat_total_charged_kwh",
    "import_total": "sensor.sdm630_gesamt_import_kwh",
    "export_total": "sensor.sdm630_gesamt_export_kwh",
    "meter_power": "sensor.sdm630_systemleistung",
    # garage PV: its own microinverter (Deye SUN-M160G4), published by powerdash
    "garage_pv": "sensor.garage_pv_leistung",
    "garage_pv_dc": "sensor.garage_pv_dc_leistung",
    "garage_pv_energy": "sensor.garage_pv_energie",
    "garage_pv_voltage": "sensor.garage_pv_spannung",
    "garage_pv_frequency": "sensor.garage_pv_frequenz",
    # NOTE: these two energy counters are NOT the same basis - the SolarEdge one is
    # evcc's own accumulation, the Deye one is the inverter's lifetime counter.
    "pv_se_energy": "sensor.evcc_stat_total_solar_k_wh_template",
}

# Klimaanlagen: (label, climate entity, kWh-heute sensor, compressor sensor)
AC = [
    ("Wohnzimmer", "climate.wzr_ap86576", "sensor.wzr_ap86576_energieverbrauch",
     "sensor.wzr_ap86576_geschatzte_leistungsaufnahme_des_kompressors"),
    ("Leas Zimmer", "climate.kevin_ap02845", "sensor.kevin_ap02845_energieverbrauch",
     "sensor.kevin_ap02845_geschatzte_leistungsaufnahme_des_kompressors"),
    ("Eltern Schlafzimmer", "climate.lea_ap27941", "sensor.lea_ap27941_energieverbrauch",
     "sensor.lea_ap27941_geschatzte_leistungsaufnahme_des_kompressors"),
    ("Dachstudio", "climate.dach_ap22393", "sensor.dach_ap22393_energieverbrauch",
     "sensor.dach_ap22393_geschatzte_leistungsaufnahme_des_kompressors"),
]


def env():
    from powerdash.ha_ws import load_env
    load_env()
    return os.environ["HASS_URL"].rstrip("/"), os.environ["HASS_TOKEN"]


def fetch_states():
    url, token = env()
    req = urllib.request.Request(url + "/api/states",
                                 headers={"Authorization": "Bearer " + token})
    with urllib.request.urlopen(req, timeout=25) as resp:
        return json.loads(resp.read().decode())


def discover(states):
    """Return (plugs, skipped) where a plug is {name, switch, power, energy}."""
    by_fname = {}
    for st in states:
        fn = (st.get("attributes") or {}).get("friendly_name")
        if fn:
            by_fname.setdefault(fn.strip(), []).append(st)

    power_re = re.compile(r"^(?P<base>.+?)\s+Leistung$")
    energy_re = re.compile(r"^(?P<base>.+?)\s+Summe verbraucht$")

    powers, energies = {}, {}
    for fn, sts in by_fname.items():
        m = power_re.match(fn)
        if m:
            powers[m.group("base")] = sts[0]["entity_id"]
        m = energy_re.match(fn)
        if m:
            energies[m.group("base")] = sts[0]["entity_id"]

    plugs, skipped = [], []
    for base in sorted(set(powers) | set(energies)):
        pw, en = powers.get(base), energies.get(base)
        switches = [s["entity_id"] for s in by_fname.get(base, [])
                    if s["entity_id"].startswith("switch.")]
        if pw and en:
            plugs.append({
                "name": base.replace("Steckdose ", "").strip(),
                "full_name": base,
                "switch": switches[0] if switches else None,
                "power": pw,
                "energy": en,
            })
        else:
            skipped.append({"base": base, "power": pw, "energy": en,
                            "switch": switches[0] if switches else None})
    return plugs, skipped


# ---------------------------------------------------------------------------
# Templates. Every value is a live Jinja expression, so the tables re-render on
# state change; nothing is baked in at build time except the plug list itself.
# ---------------------------------------------------------------------------

def house_md():
    h = HOUSE
    return (
        "{% set pv = states('" + h["pv"] + "')|float(0) %}\n"
        "{% set grid = states('" + h["grid"] + "')|float(0) %}\n"
        "{% set bat = states('" + h["battery_power"] + "')|float(0) %}\n"
        "{% set soc = states('" + h["battery_soc"] + "')|float(0) %}\n"
        "{% set home = states('" + h["home"] + "')|float(0) %}\n"
        "{% set chg = states('" + h["charger_power"] + "')|float(0) %}\n"
        "{% set sdm = states('" + h["meter_power"] + "')|float(0) %}\n"
        "| Haus — jetzt | Wert |\n"
        "| :--- | ---: |\n"
        "| **Haus gesamt (inkl. Wallbox)** | "
        "**{{ (home - sdm + chg*1000)|round(0)|int }} W** |\n"
        "| davon SolarEdge-Rohwert (ohne Wallbox) | {{ home|round(0)|int }} W |\n"
        "| Garagen-Zweig netto (SDM630) | {{ sdm|round(0)|int }} W "
        "{% if sdm < 0 %}(liefert ins Haus){% else %}(aus dem Haus){% endif %} |\n"
        "| Wallbox | {{ (chg*1000)|round(0)|int }} W |\n"
        "| Netz | {{ grid|round(0)|int }} W "
        "{% if grid >= 0 %}(Bezug){% else %}(Einspeisung){% endif %} |\n"
        "| PV-Erzeugung (Dach) | {{ pv|round(0)|int }} W |\n"
        "| Hausbatterie | {{ soc|round(1) }} %"
        "{% if bat > 20 %} · {{ bat|round(0)|int }} W entlädt"
        "{% elif bat < -20 %} · {{ (-bat)|round(0)|int }} W lädt"
        "{% else %} · idle{% endif %} |\n"
        "| Stand | {{ states." + h["home"] + ".last_updated.strftime('%H:%M:%S') }} |\n"
    )


def plugs_md(plugs):
    pairs = ",\n  ".join(
        "('%s', '%s', '%s')" % (p["name"].replace("'", ""), p["power"], p["energy"])
        for p in plugs)
    return (
        "{% set plugs = [\n  " + pairs + "\n] %}\n"
        "{% set ns = namespace(items=[], tw=0.0, te=0.0, live=0) %}\n"
        "{% for name, pw, en in plugs %}\n"
        "  {% set w = states(pw)|float(0) %}\n"
        "  {% set e = states(en)|float(0) %}\n"
        "  {% if states(pw) not in ['unknown','unavailable'] %}"
        "{% set ns.live = ns.live + 1 %}{% endif %}\n"
        "  {% set ns.items = ns.items + [[name, w, e, states(pw)]] %}\n"
        "  {% set ns.tw = ns.tw + w %}\n"
        "  {% set ns.te = ns.te + e %}\n"
        "{% endfor %}\n"
        "Σ der {{ plugs|count }} Messsteckdosen: **{{ ns.tw|round(1) }} W** "
        "({{ ns.live }} live, {{ plugs|count - ns.live }} ohne Messwert) · "
        "kumuliert **{{ ns.te|round(1) }} kWh**\n\n"
        "| # | Steckdose | Leistung | Verbraucht | Anteil |\n"
        "| ---: | :--- | ---: | ---: | ---: |\n"
        "{% for it in ns.items|sort(attribute='1', reverse=true) %}"
        "| {{ loop.index }} | {{ it[0] }} "
        "| {% if it[3] in ['unknown','unavailable'] %}—{% else %}{{ it[1]|round(1) }} W"
        "{% endif %} "
        "| {{ it[2]|round(1) }} kWh "
        "| {% if ns.tw > 0 %}{{ (it[1] / ns.tw * 100)|round(1) }} %{% else %}—{% endif %} |\n"
        "{% endfor %}"
        "| | **Σ Summe** | **{{ ns.tw|round(1) }} W** | **{{ ns.te|round(1) }} kWh** "
        "| {{ ns.live }}/{{ plugs|count }} live |\n"
    )


def ac_md():
    state_map = {
        "off": "aus", "heat": "heizt", "cool": "kühlt", "heat_cool": "auto",
        "auto": "auto", "dry": "entfeuchtet", "fan_only": "Lüftung",
        "unavailable": "n/a", "unknown": "n/a",
    }
    rows = []
    for label, clim, kwh, comp in AC:
        conds = "".join(
            "{%% if states('%s') == '%s' %%}%s{%% endif %%}"
            % (clim, key, value) for key, value in state_map.items())
        rows.append(
            "| " + label + " | " + conds + " "
            "| {{ (states('" + comp + "')|float(0) * 1000)|round(0)|int }} W "
            "| {{ states('" + kwh + "')|float(0)|round(2) }} kWh |")
    return (
        "| Raum | Zustand | Kompressor | Heute |\n"
        "| :--- | :--- | ---: | ---: |\n"
        + "\n".join(rows) + "\n"
    )


def garage_md():
    h = HOUSE
    return (
        "| Wallbox & Garage (SDM630) | Wert |\n"
        "| :--- | ---: |\n"
        "| Wallbox jetzt | {{ (states('" + h["charger_power"] + "')|float(0) * 1000)"
        "|round(0)|int }} W |\n"
        "| Garage netto | {{ states('" + h["meter_power"] + "')|float(0)|round(0)|int }} W |\n"
        "| Garage Bezug gesamt | {{ states('" + h["import_total"] + "')|float(0)|round(1) }} kWh |\n"
        "| Garage Einspeisung gesamt | {{ states('" + h["export_total"] + "')|float(0)|round(1) }} kWh |\n"
        "| Auto geladen gesamt | {{ states('" + h["charger_energy"] + "')|float(0)|round(1) }} kWh |\n"
    )


def garage_pv_md():
    h = HOUSE
    return (
        "| Garagen-PV (Deye SUN-M160G4) | Wert |\n"
        "| :--- | ---: |\n"
        "| **AC-Leistung** | **{{ states('" + h["garage_pv"] + "')|float(0)|round(1) }} W** |\n"
        "| DC-Leistung (4 Eingänge) | {{ states('" + h["garage_pv_dc"] + "')|float(0)|round(1) }} W |\n"
        "| Wirkungsgrad DC→AC | {% set dc = states('" + h["garage_pv_dc"] + "')|float(0) %}"
        "{% set ac = states('" + h["garage_pv"] + "')|float(0) %}"
        "{% if dc > 0 %}{{ (ac / dc * 100)|round(1) }} %{% else %}—{% endif %} |\n"
        "| Netzspannung | {{ states('" + h["garage_pv_voltage"] + "')|float(0)|round(1) }} V |\n"
        "| Frequenz | {{ states('" + h["garage_pv_frequency"] + "')|float(0)|round(2) }} Hz |\n"
        "| Energie gesamt | {{ states('" + h["garage_pv_energy"] + "')|float(0)|round(2) }} kWh |\n"
    )


def pv_power_md():
    """Both PV sources side by side - what the SolarEdge app structurally cannot show."""
    h = HOUSE
    return (
        "{% set se = states('" + h["pv"] + "')|float(0) %}\n"
        "{% set deye = states('" + h["garage_pv"] + "')|float(0) %}\n"
        "{% set sdm = states('" + h["meter_power"] + "')|float(0) %}\n"
        "{% set total = se + deye %}\n"
        "| PV — jetzt | Wert |\n"
        "| :--- | ---: |\n"
        "| **PV gesamt** | **{{ total|round(0)|int }} W** |\n"
        "| SolarEdge (Dach) | {{ se|round(0)|int }} W |\n"
        "| Garagen-PV (Deye) | {{ deye|round(0)|int }} W |\n"
        "| Garagen-Zweig netto (SDM630) | {{ sdm|round(0)|int }} W |\n"
        "| Differenz jetzt (enthält Deye-Verzögerung) | {{ (deye + sdm)|round(0)|int }} W |\n"
        "| Anteil Garagen-PV | {% if total > 0 %}{{ (deye / total * 100)|round(1) }} %"
        "{% else %}—{% endif %} |\n"
    )


def pv_energy_md():
    h = HOUSE
    return (
        "| PV — Ertrag | kWh |\n"
        "| :--- | ---: |\n"
        "| SolarEdge (Dach) | {{ states('" + h["pv_se_energy"] + "')|float(0)|round(1) }} |\n"
        "| Garagen-PV (Deye) | {{ states('" + h["garage_pv_energy"] + "')|float(0)|round(2) }} |\n"
    )


def note_md(text):
    return "<small>" + text + "</small>\n"


# The markdown card's body field is `content` - NOT `text`. An unknown key makes
# HA render the card as "Konfigurationsfehler", which is exactly what happened
# when this file used "text". `entity_id` limits re-renders to the entities the
# template actually reads (docs: markdown card), and `card_size` is the documented
# hint for template-heavy cards so the layout does not jump around.
def md(content, entities=None, card_size=None):
    card = {"type": "markdown", "content": content}
    if entities:
        card["entity_id"] = sorted(set(entities))
    if card_size:
        card["card_size"] = card_size
    return card


HOUSE_ENT = [HOUSE[k] for k in
             ("pv", "grid", "battery_power", "battery_soc", "home",
              "charger_power", "meter_power")]
GARAGE_ENT = [HOUSE[k] for k in
              ("charger_power", "meter_power", "import_total", "export_total",
               "charger_energy")]
GARAGE_PV_ENT = [HOUSE[k] for k in
                 ("garage_pv", "garage_pv_dc", "garage_pv_energy",
                  "garage_pv_voltage", "garage_pv_frequency")]
AC_ENT = [e for _l, clim, kwh, comp in AC for e in (clim, kwh, comp)]


def plug_ents(plugs):
    return [e for p in plugs for e in (p["power"], p["energy"])]


def build(plugs):
    sums = [
        {"type": "heading", "heading": "Haus — jetzt", "heading_style": "title"},
        md(house_md(), HOUSE_ENT, 7),
        md(note_md(
            "Der SolarEdge sieht nur seine eigene Erzeugung. Die Garagen-PV speist "
            "<b>hinter</b> seinem Messpunkt ins Haus ein und senkt damit den gemessenen "
            "Netzfluss, ohne in seiner Erzeugung aufzutauchen — sein Rohwert ist deshalb "
            "um den Garagen-Zweig zu klein. Deshalb: "
            "<b>Haus gesamt = SolarEdge-Rohwert − Garagen-Zweig netto + Wallbox</b>. "
            "Gegenprobe: der Wert muss über der Summe der Messsteckdosen liegen (tut er).")),
    ]

    plugs_section = [
        {"type": "heading", "heading": "Steckdosen (alle Messstecker)",
         "heading_style": "title"},
        md(plugs_md(plugs), plug_ents(plugs), 20),
        md(note_md(
            "Vergleichsbasis ist <b>Haus gesamt (inkl. Wallbox)</b> oben. Der Unterschied "
            "wird bewusst nicht als „Restlast“ ausgewiesen: beide Zahlen stammen aus "
            "verschiedenen Messketten (Hauswert aus dem SolarEdge, Stecker per Zigbee, "
            "die nur bei Änderung senden und bei kleiner Last ±1–2 W Toleranz haben), "
            "und die Summe der Stecker lag schon über dem Hauswert.")),
    ]

    ac_section = [
        {"type": "heading", "heading": "Klimaanlagen", "heading_style": "title"},
        md(ac_md(), AC_ENT, 6),
        md(note_md(
            "Heute = kWh des laufenden Messzyklus der Klimaanlage, kein "
            "Lebensdauer-Zähler wie bei den Steckdosen.")),
    ]

    garage_section = [
        {"type": "heading",
         "heading": "Wallbox & Garage (SDM630 = Ladepunkt + Garagen-PV)",
         "heading_style": "title"},
        md(garage_md(), GARAGE_ENT, 6),
        md(note_md(
            "Der SDM630 sitzt nur im Garagenzweig (Wallbox + Garagen-PV, beide dahinter) "
            "und ist <b>nicht</b> der Hauszähler. Aktiv nur auf L2; L1/L3 sind 0 A. "
            "Negative Werte = der Garagenzweig speist ins Haus ein.")),
    ]

    garage_pv_section = [
        {"type": "heading", "heading": "Garagen-PV (eigener Wechselrichter)",
         "heading_style": "title"},
        md(garage_pv_md(), GARAGE_PV_ENT, 6),
        md(note_md(
            "Eigener Mikro-Wechselrichter im Garagenzweig. Er erzeugt für das Haus, "
            "aber der SolarEdge misst nur seinen eigenen Ertrag: die Garagen-PV senkt "
            "den Netzfluss, ohne in der SolarEdge-Erzeugung zu erscheinen. Deshalb "
            "fehlt sie im Rohwert oben und wird über den Garagen-Zweig (SDM630) "
            "korrigiert. Garagen-PV und Garagen-Zweig-Netto sind nicht dasselbe: der "
            "SDM630 verrechnet Erzeugung und Garagenlasten (inkl. Wallbox) zu einem "
            "Netto-Wert — aktuell {{ states('" + HOUSE["garage_pv"] + "')|float(0)|round(0)|int }} W "
            "Erzeugung, netto {{ (states('" + HOUSE["meter_power"] + "')|float(0) * -1)"
            "|round(0)|int }} W ins Haus, der Rest verbraucht die Garage selbst.")),
    ]

    GARAGE_PV_ENT.append(HOUSE["meter_power"])

    pv_section = [
        {"type": "heading", "heading": "PV — beide Anlagen", "heading_style": "title"},
        md(pv_power_md(), [HOUSE["pv"], HOUSE["garage_pv"], HOUSE["meter_power"]], 7),
        md(pv_energy_md(), [HOUSE["pv_se_energy"], HOUSE["garage_pv_energy"]], 4),
        md(note_md(
            "Zwei getrennte Anlagen: SolarEdge auf dem Dach, Deye in der Garage — genau "
            "das, was die SolarEdge-App strukturell nicht zeigen kann. Die Erträge haben "
            "<b>nicht dieselbe Basis</b>: der SolarEdge-Wert ist die evcc-Statistik (seit "
            "evcc aufzeichnet), der Deye-Wert der Lebensdauer-Zähler des Wechselrichters "
            "(seit Installation) — deshalb bewusst keine Summe. Zwei Vorbehalte, beide "
            "gemessen: (1) die SolarEdge-Leistung ist bei Sättigung unzuverlässig, sie "
            "meldet zeitweise über 4600 W trotz 1-phasiger Begrenzung; (2) der Deye liefert "
            "seine Register nur ca. alle 4 Minuten neu (Wert kann also veraltet sein), und "
            "sein Energie-Zähler läuft 8× langsamer als seine eigene Leistungsanzeige — die "
            "Skalierung dieses Zählers ist ungeklärt und der kWh-Wert unten entsprechend "
            "unsicher. Die Zeile „Differenz jetzt“ ist deshalb nur eine Momentaufnahme. "
            "Belastbarer ist der Stundenmittelwert: er lag bei rund <b>45 W</b> — das ist "
            "der Garagenverbrauch (WLAN-Router, Wallbox-Standby, Torantrieb) plus eine "
            "kleine Abweichung des Deye-Registers; der SDM630 ist in sich konsistent "
            "(Zähler ↔ Leistung ≈ 1.00) und daher die verlässlichere der beiden Quellen.")),
    ]

    overview = {
        "title": "Tabellen",
        "path": "tabellen",
        "icon": "mdi:table",
        "type": "sections",
        "max_columns": 4,
        "sections": [
            {"type": "grid", "column_span": 2, "cards": sums},
            {"type": "grid", "column_span": 2, "cards": pv_section},
            {"type": "grid", "column_span": 2, "cards": garage_section},
            {"type": "grid", "column_span": 2, "cards": garage_pv_section},
            {"type": "grid", "column_span": 4, "cards": plugs_section},
            {"type": "grid", "column_span": 2, "cards": ac_section},
        ],
    }

    history = {
        "title": "Verlauf",
        "path": "verlauf",
        "icon": "mdi:chart-line",
        "type": "sections",
        "max_columns": 2,
        "sections": [
            {"type": "grid", "cards": [
                {"type": "heading", "heading": "Leistung — letzte 24 h",
                 "heading_style": "title"},
                {"type": "statistics-graph", "title": "Hausverbrauch (W)",
                 "entities": [HOUSE["home"]], "hours_to_show": 24, "chart_type": "line"},
                {"type": "statistics-graph", "title": "PV: SolarEdge + Garagen-PV (W)",
                 "entities": [HOUSE["pv"], HOUSE["garage_pv"]],
                 "hours_to_show": 24, "chart_type": "line"},
                {"type": "statistics-graph", "title": "PV / Netz / Batterie (W)",
                 "entities": [HOUSE["pv"], HOUSE["grid"], HOUSE["battery_power"]],
                 "hours_to_show": 24, "chart_type": "line"},
            ]},
            {"type": "grid", "cards": [
                {"type": "heading", "heading": "Zählerstände (kumuliert)",
                 "heading_style": "title"},
                {"type": "statistics-graph", "title": "Steckdosen (kWh)",
                 "entities": [p["energy"] for p in sorted(plugs, key=lambda x: x["name"])],
                 "hours_to_show": 168, "chart_type": "line"},
            ]},
        ],
    }

    details_entities = []
    for p in sorted(plugs, key=lambda x: x["name"]):
        if p["switch"]:
            details_entities.append(p["switch"])
        details_entities.append(p["power"])
        details_entities.append(p["energy"])

    details = {
        "title": "Schalter",
        "path": "schalter",
        "icon": "mdi:toggle-switch",
        "type": "sections",
        "max_columns": 2,
        "sections": [
            {"type": "grid", "cards": [
                {"type": "heading", "heading": "Steckdosen schalten",
                 "heading_style": "title"},
                {"type": "entities", "state_color": True, "entities": details_entities},
                md(note_md(
                    "Hier lassen sich die Steckdosen direkt schalten; die Tabelle im "
                    "Reiter „Tabellen“ ist nur Anzeige.")),
            ]},
            {"type": "grid", "cards": [
                {"type": "heading", "heading": "Haus (SolarEdge) & Garage (SDM630)",
                 "heading_style": "title"},
                {"type": "entities", "entities": [
                    HOUSE["home"], HOUSE["grid"], HOUSE["pv"],
                    HOUSE["battery_power"], HOUSE["battery_soc"],
                    HOUSE["meter_power"], HOUSE["charger_power"],
                    HOUSE["charger_energy"], HOUSE["import_total"], HOUSE["export_total"],
                    HOUSE["pv_se_energy"],
                    HOUSE["garage_pv"], HOUSE["garage_pv_dc"],
                    HOUSE["garage_pv_voltage"], HOUSE["garage_pv_frequency"],
                    HOUSE["garage_pv_energy"],
                ]},
                {"type": "heading", "heading": "Klimaanlagen", "heading_style": "title"},
                {"type": "entities", "entities": [
                    e for _l, clim, kwh, comp in AC for e in (clim, kwh, comp)
                ]},
            ]},
        ],
    }

    return {"title": "Strom", "views": [overview, history, details]}


def main():
    states = fetch_states()
    plugs, skipped = discover(states)
    print("discovered plugs: %d" % len(plugs))
    for p in plugs:
        print("  %-28s switch=%-38s W=%-46s kWh=%s"
              % (p["name"], p["switch"] or "-", p["power"], p["energy"]))
    if skipped:
        print("skipped (incomplete triplet):")
        for s in skipped:
            print("  %-28s power=%s energy=%s switch=%s"
                  % (s["base"], s["power"], s["energy"], s["switch"]))

    cfg = build(plugs)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n")
    print("\nwrote %s (%d bytes, %d views)"
          % (OUT, OUT.stat().st_size, len(cfg["views"])))
    return 0


if __name__ == "__main__":
    sys.exit(main())