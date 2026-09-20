#!/usr/bin/env python3
"""Is the SolarEdge house figure understated by the garage PV?

The SolarEdge's generation term contains only ITS OWN output, but the garage PV
also feeds the house - behind the connection-point meter, so it reduces the metered
grid flow without appearing in the generation term. That makes evcc's homePower too
LOW by the garage branch's net contribution.

Defines the zones explicitly and checks the corrected figure against the plug sum:

    homePower           = house_excl_garage + garage_loads + charger - garage_PV
    sdm630 (garage net) = charger + garage_loads - garage_PV      [negative = feeds house]
    => house_excl_garage = homePower - sdm630
    => Haus gesamt (excl. battery charge) = house_excl_garage + charger

Independent check: Haus gesamt must be >= the sum of the metering plugs.
"""
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from powerdash.env import ha_token, ha_url

PLUG_IDS = [
    "stckdose_leistung", "steckdose_arbeitszimmer_wt_leistung",
    "steckdose_knetzwerk_server_leistung", "steckdose_kuhlschrank_leistung",
    "steckdose_lufttrockner_leistung", "steckdose_mascha_leistung",
    "tz3000_gjnozsaz_ts011f_leistung", "tz3000_gjnozsaz_ts011f_leistung_2",
    "tz3000_gjnozsaz_ts011f_leistung_3", "tz3000_gjnozsaz_ts011f_leistung_4",
    "tz3000_gjnozsaz_ts011f_leistung_5", "tz3000_gjnozsaz_ts011f_leistung_6",
    "tz3000_gjnozsaz_ts011f_leistung_7", "tz3000_gjnozsaz_ts011f_leistung_8",
    "tz3000_gjnozsaz_ts011f_leistung_9", "tz3000_gjnozsaz_ts011f_leistung_10",
]
# NB: a bare dict literal inside {{ }} makes HA's Jinja parser fail with
# "unexpected '}'" - build the mapping with dict() and a list comprehension.
TEMPLATE = (
    '{{ dict('
    'home=states("sensor.evcc_home_power")|float(0),'
    'charger_kw=states("sensor.evcc_go_e_charger_charge_power")|float(0),'
    'sdm=states("sensor.sdm630_systemleistung")|float(0),'
    'pv=states("sensor.evcc_pv_power")|float(0),'
    'grid=states("sensor.evcc_grid_power")|float(0),'
    'battery=states("sensor.evcc_battery_power")|float(0),'
    'garage_pv=states("sensor.garage_pv_leistung")|float(0),'
    'plugs=[' + ",".join('states("sensor.%s")|float(0)' % e for e in PLUG_IDS) +
    ']|sum) | tojson }}'
)


def main():
    req = urllib.request.Request(ha_url() + "/api/template",
                                 data=json.dumps({"template": TEMPLATE}).encode(),
                                 method="POST",
                                 headers={"Authorization": "Bearer " + ha_token(),
                                          "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            v = json.loads(r.read().decode())
    except urllib.error.HTTPError as exc:
        raise SystemExit("template failed: HTTP %s\n%s\n\nTEMPLATE:\n%s"
                         % (exc.code, exc.read().decode(errors="replace"), TEMPLATE))

    home = v["home"]
    charger = v["charger_kw"] * 1000.0
    sdm = v["sdm"]
    plugs = v["plugs"]

    house_excl = home - sdm
    haus_gesamt = house_excl + charger

    print("SolarEdge/evcc:")
    print("  homePower (ohne Wallbox)      : %8.1f W" % home)
    print("  charger                       : %8.1f W" % charger)
    print("  PV / Netz / Batterie          : %8.1f / %8.1f / %8.1f W"
          % (v["pv"], v["grid"], v["battery"]))
    print("Garagen-Zweig (SDM630, unabhaengig):")
    print("  netto                         : %8.1f W  (>0 = bezieht, <0 = liefert ins Haus)" % sdm)
    print("  Garagen-PV (Deye)             : %8.1f W" % v["garage_pv"])
    print("abgeleitet:")
    print("  Haus ohne Garagen-Zweig       : %8.1f W" % house_excl)
    print("  HAUS GESAMT (inkl. Wallbox)   : %8.1f W" % haus_gesamt)
    print("  Summe der Messsteckdosen      : %8.1f W" % plugs)
    print()
    if haus_gesamt >= plugs:
        print("  => plausibel: Gesamt (%.0f W) >= Stecker (%.0f W), Rest ungemessen"
              % (haus_gesamt, plugs))
    else:
        print("  => WIDERSPRUCH: Gesamt (%.0f W) < Stecker (%.0f W)" % (haus_gesamt, plugs))


if __name__ == "__main__":
    main()