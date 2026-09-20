#!/usr/bin/env python3
"""Credential loading, isolated so scripts that need no websocket client don't
pull one in. The token is read from the environment or ~/.hermes/.env and is never
printed or written anywhere.
"""

import os
from pathlib import Path

ENV_FILE = Path.home() / ".hermes" / ".env"


def load_env(force=False):
    """Populate HASS_URL / HASS_TOKEN from ~/.hermes/.env when not already set."""
    if not force and os.environ.get("HASS_URL") and os.environ.get("HASS_TOKEN"):
        return
    if not ENV_FILE.exists():
        return
    for line in ENV_FILE.read_text(errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip().strip('"').strip("'")
        if key.startswith("HASS_") and (force or not os.environ.get(key)):
            os.environ[key] = val


def ha_url():
    load_env()
    return os.environ["HASS_URL"].rstrip("/")


def ha_token():
    load_env()
    return os.environ["HASS_TOKEN"]