#!/usr/bin/env python3
"""Minimal Home Assistant WebSocket API client (stdlib + websocket-client).

Used to inspect and write Lovelace dashboards, which HA only exposes over the
websocket API (there is no REST endpoint for dashboard config).

Credentials are read from the environment, falling back to ~/.hermes/.env.
The token is never printed, logged, or embedded in any generated file.
"""

import json
import os
from pathlib import Path

import websocket  # websocket-client

from powerdash.env import load_env  # noqa: F401  (re-exported for callers)

ENV_FILE = Path.home() / ".hermes" / ".env"


class HAWebSocket:
    """Blocking HA websocket client. Use as a context manager."""

    def __init__(self, timeout=20):
        load_env()
        base = os.environ["HASS_URL"].rstrip("/")
        self.url = base.replace("https://", "wss://").replace("http://", "ws://") + "/api/websocket"
        self.ws = websocket.create_connection(self.url, timeout=timeout)
        self._id = 0
        hello = json.loads(self.ws.recv())
        if hello.get("type") != "auth_required":
            raise RuntimeError("unexpected hello: %s" % hello.get("type"))
        self.ws.send(json.dumps({"type": "auth", "access_token": os.environ["HASS_TOKEN"]}))
        res = json.loads(self.ws.recv())
        if res.get("type") != "auth_ok":
            raise RuntimeError("auth failed: %s" % res.get("type"))
        self.user = (res.get("user") or {}).get("name")
        self.version = res.get("ha_version")

    def call(self, msg_type, **kwargs):
        """Send one command, return its result. Raises on a WS error reply."""
        self._id += 1
        mid = self._id
        payload = {"id": mid, "type": msg_type}
        payload.update(kwargs)
        self.ws.send(json.dumps(payload))
        while True:
            res = json.loads(self.ws.recv())
            if res.get("id") != mid:
                continue  # ignore events / other traffic
            if not res.get("success"):
                err = res.get("error") or {}
                raise RuntimeError("%s failed: %s %s" % (msg_type, err.get("code"), err.get("message")))
            return res.get("result")

    def close(self):
        try:
            self.ws.close()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


def render_template(template):
    """Render a Jinja template through HA (same engine as the UI)."""
    with HAWebSocket() as ha:
        return ha.call("render_template", template=template)


if __name__ == "__main__":
    with HAWebSocket() as ha:
        print("connected: HA %s as %r" % (ha.version, ha.user))
        for path in ("/api/",):
            pass
        print("dashboards:", json.dumps(ha.call("lovelace/dashboards/list"), ensure_ascii=False, indent=2))
        try:
            print("resources:", json.dumps(ha.call("lovelace/resources"), ensure_ascii=False, indent=2))
        except Exception as exc:
            print("resources unavailable:", exc)