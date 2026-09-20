#!/usr/bin/env python3
"""Differential test for the comparison CSV.

The row appended to logs/se_vs_garage.csv is the data behind the SDM/Deye
analysis, so its column set, rounding and line endings matter as much as the
published JSON does.

The Python side is driven through its own `log_sample()` with the module's LOG
constant redirected to a temp file (the original file is never modified and the
real CSV is never touched). The Rust side runs the real poller against the same
fake Home Assistant and the same stub logger, with --log-csv pointed at a temp
file. Both rows are then compared field by field, ignoring only the timestamp.

Usage: python3 tools/differential_csv.py
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import differential_test as dt  # noqa: E402

DRIVER = r'''
import json, os, sys, tempfile
from pathlib import Path
sys.path.insert(0, os.environ["PROJECT"])
import powerdash.compare_se as cs
cs.LOG = Path(sys.argv[1])
# the same values the stub logger produces, so both sides log the same row
row = cs.log_sample({"ac_power_w": 253.5, "dc_power_w": 401.1})
print("log_sample returned: %s" % (row,))
print("--- file ---")
print(cs.LOG.read_text(), end="")
'''


def main():
    workdir = tempfile.mkdtemp(prefix="deyepv-csv-")
    stub_port = dt.free_port()
    stub = dt.start_stub(stub_port)
    try:
        if not dt.wait_port(stub_port):
            print("stub never came up")
            return 2

        # a fake Home Assistant that *does* render templates, so both sides log
        ha = WorkingFakeHa()

        interpreter = dt.python_interpreter()
        driver_path = os.path.join(workdir, "driver.py")
        with open(driver_path, "w") as fh:
            fh.write(DRIVER)

        py_csv = os.path.join(workdir, "python.csv")
        env = dict(os.environ)
        env["HASS_URL"] = "http://127.0.0.1:%d" % ha.port
        env["HASS_TOKEN"] = "csv-differential-token"
        env["PROJECT"] = dt.HA_PROJECT
        py_run = subprocess.run([interpreter, driver_path, py_csv], env=env,
                                cwd=workdir, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, timeout=60)
        print("Python side (%s):" % os.path.basename(interpreter))
        print(py_run.stdout)

        rs_csv = os.path.join(workdir, "rust.csv")
        rs_env = dict(os.environ)
        rs_env["HASS_URL"] = "http://127.0.0.1:%d" % ha.port
        rs_env["HASS_TOKEN"] = "csv-differential-token"
        rs_run = subprocess.run(
            [dt.RS_POLLER, "--host", "127.0.0.1", "--port", str(stub_port),
             "--log-csv", rs_csv, "--once"],
            env=rs_env, cwd=workdir, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, timeout=60)
        print("Rust side:")
        print(rs_run.stdout)

        if not (os.path.exists(py_csv) and os.path.exists(rs_csv)):
            print("one side wrote no CSV: python=%s rust=%s"
                  % (os.path.exists(py_csv), os.path.exists(rs_csv)))
            return 1

        py_text = open(py_csv, "rb").read().decode()
        rs_text = open(rs_csv, "rb").read().decode()
        print("-" * 78)
        py_lines = [l for l in py_text.split("\r\n") if l]
        rs_lines = [l for l in rs_text.split("\r\n") if l]

        ok = True
        if py_lines[0] != rs_lines[0]:
            ok = False
            print("header differs:\n  python: %s\n  rust  : %s" % (py_lines[0], rs_lines[0]))

        py_fields = py_lines[1].split(",")
        rs_fields = rs_lines[1].split(",")
        ts_pattern = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")
        for name, py_value, rs_value in zip(dt.__dict__.get("FIELDS", [
                "ts", "se_pv_w", "evcc_home_w", "home_total_w", "charger_w",
                "grid_w", "battery_w", "garage_pv_w", "garage_pv_dc_w",
                "excess_over_4600"]), py_fields, rs_fields):
            if name == "ts":
                fmt_ok = bool(ts_pattern.match(py_value) and ts_pattern.match(rs_value))
                print("  %-16s %s   (python %s / rust %s)" % (name, "MATCH" if fmt_ok else "DIFFER",
                                                              py_value, rs_value))
                ok = ok and fmt_ok
                continue
            same = py_value == rs_value
            ok = ok and same
            print("  %-16s %-7s python=%-10s rust=%s"
                  % (name, "MATCH" if same else "DIFFER", py_value, rs_value))

        print("-" * 78)
        print("CRLF line endings: python=%s rust=%s"
              % ("\r\n" in py_text, "\r\n" in rs_text))
        print("header+rows: python=%d rust=%d" % (len(py_lines), len(rs_lines)))
        print("rows identical" if ok else "ROWS DIFFER")
        return 0 if ok else 1
    finally:
        stub.terminate()
        time.sleep(0.2)
        stub.kill()
        shutil.rmtree(workdir, ignore_errors=True)


class WorkingFakeHa(dt.FakeHa):
    """A fake HA that renders templates instead of refusing them."""

    def __init__(self):
        import json as _json
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        import socket

        self.publishes = []
        self.lock = threading.Lock()
        self.port = dt.free_port()
        recorder = self
        sample = {"se_pv": 1600.5, "home": 2958.9, "grid": -0.9,
                  "battery": 909.0, "charger_kw": 1.38}

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length).decode()
                if "/api/template" in self.path:
                    self._send(200, _json.dumps(sample))
                    return
                if "/api/services/mqtt/publish" in self.path:
                    try:
                        parsed = _json.loads(body)
                    except Exception:
                        parsed = {}
                    with recorder.lock:
                        recorder.publishes.append(parsed)
                    self._send(200, "{}")
                    return
                self._send(404, "{}")

            def _send(self, status, body):
                raw = body.encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", self.port), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()


if __name__ == "__main__":
    sys.exit(main())
