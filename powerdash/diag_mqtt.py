#!/usr/bin/env python3
"""Diagnose why mqtt.publish fails: does the MQTT integration exist, and what does
the 400 body say?"""
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from powerdash.env import ha_token, ha_url


def get(path):
    req = urllib.request.Request(ha_url() + path,
                                 headers={"Authorization": "Bearer " + ha_token()})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode())


def post(path, payload):
    req = urllib.request.Request(ha_url() + path, data=json.dumps(payload).encode(),
                                 method="POST",
                                 headers={"Authorization": "Bearer " + ha_token(),
                                          "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, r.read().decode()[:200]
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode(errors="replace")[:400]


def main():
    raw = get("/api/services")
    print("first entry keys:", sorted(raw[0].keys()) if raw else "none")
    domains = {}
    for svc in raw:
        name = svc.get("service") or svc.get("services") or svc.get("id") or svc.get("domain")
        domains.setdefault(svc.get("domain"), []).append(name)
    print("mqtt domain present:", "mqtt" in domains)
    if "mqtt" in domains:
        print("mqtt services:", sorted(str(x) for x in domains["mqtt"]))
    print("\nother publishing-ish domains:",
          [d for d in domains if d in ("notify", "persistent_notification", "shell_command",
                                       "python_script", "input_number", "input_text")])

    code, body = post("/api/services/mqtt/publish",
                      {"topic": "powerdash/selftest", "payload": "hello", "qos": 0})
    print("\nPOST mqtt/publish -> %s %s" % (code, body))

    code, body = post("/api/services/mqtt/publish",
                      {"topic": "powerdash/selftest2",
                       "payload_template": "hello2", "qos": 0})
    print("POST with payload_template -> %s %s" % (code, body))


if __name__ == "__main__":
    main()