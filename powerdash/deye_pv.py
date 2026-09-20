#!/usr/bin/env python3
"""Publish the garage PV (Deye SUN-M160G4 via its SolarMAN logger) into Home Assistant.

No MQTT credentials and no HA file access: state and discovery messages are pushed
through Home Assistant's own mqtt.publish service over the REST API, so HA is the
MQTT client and this script only needs a token it already has.

    python3 -m powerdash.deye_pv --test        # publish to a plain topic, no entity
    python3 -m powerdash.deye_pv --discovery   # create the HA entities (retained)
    python3 -m powerdash.deye_pv --once        # one reading, published
    python3 -m powerdash.deye_pv               # loop

Register map: see the deye-solarman-logger skill. Values are read-only.
"""

import json
import os
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from powerdash.env import load_env

HOST = "192.168.178.33"
SERIAL = 3842831288          # the serial the device reports in its own replies
# 30 s, not 15: this logger accepts one session at a time, and opening a fresh
# connection every 15 s got refused, which left the entities unavailable.
INTERVAL_S = 30
_INV = None
_LAST_OK = None
DEVICE = {"identifiers": ["deye_sun_m160g4_garage"],
          "name": "Garage PV (Deye SUN-M160G4)",
          "manufacturer": "Deye", "model": "SUN-M160G4-EU-Q0"}
STATE_TOPIC = "powerdash/garage_pv/state"
AVAILABILITY_TOPIC = "powerdash/garage_pv/status"

ENTITIES = [
    ("sensor", "garage_pv_power", "garage_pv_leistung", {
        "name": "Garage PV Leistung", "device_class": "power",
        "unit_of_measurement": "W", "state_class": "measurement",
        "value_template": "{{ value_json.ac_power_w }}"}),
    ("sensor", "garage_pv_energy", "garage_pv_energie", {
        "name": "Garage PV Energie gesamt", "device_class": "energy",
        "unit_of_measurement": "kWh", "state_class": "total_increasing",
        "value_template": "{{ value_json.energy_kwh }}"}),
    ("sensor", "garage_pv_voltage", "garage_pv_spannung", {
        "name": "Garage PV Spannung", "device_class": "voltage",
        "unit_of_measurement": "V", "state_class": "measurement",
        "value_template": "{{ value_json.ac_voltage_v }}"}),
    ("sensor", "garage_pv_frequency", "garage_pv_frequenz", {
        "name": "Garage PV Frequenz", "device_class": "frequency",
        "unit_of_measurement": "Hz", "state_class": "measurement",
        "value_template": "{{ value_json.frequency_hz }}"}),
    ("sensor", "garage_pv_dc_power", "garage_pv_dc_leistung", {
        "name": "Garage PV DC-Leistung", "device_class": "power",
        "unit_of_measurement": "W", "state_class": "measurement",
        "value_template": "{{ value_json.dc_power_w }}"}),
]

# without an explicit object_id HA builds the entity_id from the device name plus
# the entity name, giving sensor.garage_pv_deye_sun_m160g4_garage_pv_leistung
# instead of sensor.garage_pv_leistung.
ENTITY_IDS = {"sensor.garage_pv_leistung", "sensor.garage_pv_energie",
              "sensor.garage_pv_spannung", "sensor.garage_pv_frequenz",
              "sensor.garage_pv_dc_leistung"}


def read_deye():
    """One read of the whole exposed map; returns the decoded measurements.

    Reuses a single persistent connection: this logger refuses new sessions while
    another is open (observed as NoSocketAvailableError / 'Connection closed on
    read'), so reconnecting on every poll is what breaks it. The cached client is
    dropped on error so the next attempt starts clean.
    """
    global _INV
    from pysolarmanv5 import PySolarmanV5
    if _INV is None:
        _INV = PySolarmanV5(HOST, SERIAL, port=8899, mb_slave_id=1,
                            socket_timeout=10, auto_reconnect=True)
    try:
        v = _INV.read_holding_registers(0x0000, 125)
    except Exception:
        _INV = None
        raise
    dc = []
    for off in (0x006D, 0x006F, 0x0071, 0x0073):
        dc.append({"v": v[off] / 10.0, "a": v[off + 1] / 10.0})
    return {
        "ac_power_w": round(v[0x0056] / 10.0, 1),
        "ac_voltage_v": round(v[0x005B] / 10.0, 1),
        "frequency_hz": round(v[0x005D] / 100.0, 2),
        "energy_kwh": round(v[0x003F] / 100.0, 2),
        "dc_inputs": dc,
        "dc_power_w": round(sum(d["v"] * d["a"] for d in dc), 1),
        "logger_serial": "".join(chr((v[0x0003 + i] >> s) & 0xFF)
                                 for i in range(5) for s in (8, 0)),
    }


def ha_publish(topic, payload, retain=False):
    load_env()
    url = os.environ["HASS_URL"].rstrip("/") + "/api/services/mqtt/publish"
    body = json.dumps({"topic": topic, "payload": payload, "retain": retain,
                       "qos": 0}).encode()
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Authorization": "Bearer " + os.environ["HASS_TOKEN"],
        "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.status


def publish_discovery(dry=False, cleanup=False):
    for component, topic_id, object_id, extra in ENTITIES:
        topic = "homeassistant/%s/powerdash/%s/config" % (component, topic_id)
        if cleanup:
            # an EMPTY retained payload is how MQTT discovery deletes an entity
            code = ha_publish(topic, "", retain=True)
            print("  cleanup   %-58s -> %s" % (topic, code))
            continue
        cfg = {"name": extra["name"], "unique_id": "powerdash_%s" % topic_id,
               "object_id": object_id,
               "state_topic": STATE_TOPIC, "device": DEVICE,
               "availability_topic": AVAILABILITY_TOPIC,
               "payload_available": "online", "payload_not_available": "offline"}
        cfg.update({k: v for k, v in extra.items() if k != "name"})
        if dry:
            print("  [dry-run] %s -> %s\n            %s"
                  % (topic, object_id, json.dumps(cfg, ensure_ascii=False)))
            continue
        code = ha_publish(topic, json.dumps(cfg), retain=True)
        print("  discovery %-58s -> %s (entity_id %s.%s)" % (topic, code, component, object_id))
    if dry:
        print("  [dry-run] powerdash/garage_pv/status -> online (retained)")
        return
    if not cleanup:
        print("  availability -> %s" % ha_publish("powerdash/garage_pv/status", "online", True))


def push_once():
    """One read of the logger, published to the state topic. Returns True on success.

    Availability is retained state, so a single failed poll leaves the entities
    'unavailable' until something publishes 'online' again - the recovery path has
    to republish it, not just the startup path.
    """
    global _LAST_OK
    try:
        data = read_deye()
        ha_publish(STATE_TOPIC, json.dumps(data), retain=True)
        if _LAST_OK is not True:
            ha_publish(AVAILABILITY_TOPIC, "online", retain=True)
            print("availability -> online (retained)", flush=True)
            _LAST_OK = True
        print("%s  %6.1f W AC  %6.1f W DC  %5.1f V  %5.2f Hz  %8.2f kWh"
              % (time.strftime("%H:%M:%S"), data["ac_power_w"], data["dc_power_w"],
                 data["ac_voltage_v"], data["frequency_hz"], data["energy_kwh"]),
              flush=True)
        from powerdash.compare_se import log_sample
        log_sample(data)
        return True
    except Exception as exc:
        print("%s  ERROR %s: %s" % (time.strftime("%H:%M:%S"), type(exc).__name__, exc),
              flush=True)
        try:
            ha_publish(AVAILABILITY_TOPIC, "offline", retain=True)
        except Exception:
            pass
        _LAST_OK = False
        return False


def main_loop_once():
    push_once()
    return 0


def main():
    args = sys.argv[1:]
    if "--test" in args:
        code = ha_publish("powerdash/selftest", json.dumps(read_deye()))
        print("publish to powerdash/selftest -> HTTP %s" % code)
        return 0
    if "--recreate" in args:
        # entity_id is fixed at creation, so changing object_id needs the old
        # entities deleted first: empty retained config, wait, then re-create
        print("step 1: deleting the existing entities (empty retained configs):")
        publish_discovery(cleanup=True)
        print("waiting 5 s for HA to remove them...")
        time.sleep(5)
        print("step 2: re-creating with explicit object_ids:")
        publish_discovery()
        print("step 3: first state push")
        return main_loop_once()
    if "--discovery" in args or "--dry-run" in args:
        dry = "--dry-run" in args
        print("discovery configs%s:" % (" (dry-run, nothing published)" if dry else " (retained)"))
        publish_discovery(dry=dry)
        return 0

    once = "--once" in args
    while True:
        push_once()
        if once:
            return 0
        time.sleep(INTERVAL_S)


if __name__ == "__main__":
    sys.exit(main())