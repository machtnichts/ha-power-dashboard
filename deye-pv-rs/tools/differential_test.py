#!/usr/bin/env python3
"""Differential test: the Python poller against the Rust poller.

Both implementations get their own stub SolarMAN logger (identical register
values) and their own fake Home Assistant that records every publish. Then the
same command is run on each side and the recorded sequences are compared byte for
byte - topic, payload and the retain flag.

This is stronger than "both pass the same tests": it catches divergences in the
published JSON, the discovery payloads and the availability behaviour that no
hand-written assertion would think to ask about.

The real inverter is never contacted - it accepts one session at a time and is
currently owned by the Python service.

The Python side is a *copy* with only its host and port patched, so the original
file stays exactly as it is. Its CSV side effect is neutralised for both sides by
answering /api/template with a 500, which each implementation skips identically.

Usage: python3 tools/differential_test.py
"""

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
RS_PROJECT = os.path.dirname(HERE)
HA_PROJECT = os.path.dirname(RS_PROJECT)
PY_POLLER = os.path.join(HA_PROJECT, "powerdash", "deye_pv.py")
RS_POLLER = os.path.join(RS_PROJECT, "target", "release", "deyepv")
STUB = os.path.join(RS_PROJECT, "target", "release", "stubdeye")

# the register set the Rust integration tests use, so both sides see the same
REGISTERS = [
    ("0x3", 12852), ("0x4", 12340), ("0x5", 12601), ("0x6", 12353), ("0x7", 16965),
    ("0x56", 2535), ("0x5B", 2363), ("0x5D", 4998), ("0x3F", 27656),
    ("0x6D", 2360), ("0x6E", 12), ("0x6F", 2358), ("0x70", 5),
    ("0x71", 0), ("0x72", 0), ("0x73", 0), ("0x74", 0),
]


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def wait_port(port, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        try:
            with socket.create_connection(("127.0.0.1", port), 0.25):
                return True
        except OSError:
            time.sleep(0.05)
    return False


class FakeHa:
    """Records publishes; refuses the template call so no CSV is written."""

    def __init__(self):
        self.publishes = []
        self.lock = threading.Lock()
        self.port = free_port()
        recorder = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length).decode()
                if "/api/services/mqtt/publish" in self.path:
                    try:
                        parsed = json.loads(body)
                    except Exception:
                        parsed = {}
                    with recorder.lock:
                        recorder.publishes.append(
                            (parsed.get("topic"), parsed.get("payload"),
                             bool(parsed.get("retain")))
                        )
                    self._send(200, "{}")
                    return
                if "/api/template" in self.path:
                    # 500 on purpose: both sides must skip the comparison CSV, so
                    # the real logs/se_vs_garage.csv is never touched
                    self._send(500, '{"error": "template disabled for this test"}')
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


def start_stub(port):
    args = [STUB, "--port", str(port), "--quiet"]
    for address, value in REGISTERS:
        args += ["--set", "%s=%d" % (address, value)]
    proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    wait_port(port)
    return proc


def patched_python_poller(stub_port, workdir):
    """A copy of the Python poller with only its host and port changed."""
    with open(PY_POLLER) as fh:
        source = fh.read()

    host_old = 'HOST = "192.168.178.33"'
    port_old = "port=8899,"
    if host_old not in source or port_old not in source:
        raise SystemExit(
            "the Python poller changed shape; expected %r and %r" % (host_old, port_old)
        )
    source = source.replace(host_old, 'HOST = "127.0.0.1"')
    source = source.replace(port_old, "port=%d," % stub_port)

    path = os.path.join(workdir, "deye_pv_patched.py")
    with open(path, "w") as fh:
        fh.write(source)
    return path


def python_interpreter():
    """The project's venv, because pysolarmanv5 lives there and nowhere else."""
    candidate = os.path.join(HA_PROJECT, ".venv", "bin", "python")
    return candidate if os.path.exists(candidate) else sys.executable


def run_side(name, command, ha, stub_port, workdir, extra):
    """Run one implementation and return its recorded publishes."""
    env = dict(os.environ)
    env["HASS_URL"] = "http://127.0.0.1:%d" % ha.port
    env["HASS_TOKEN"] = "differential-test-token"
    env["PYTHONPATH"] = HA_PROJECT

    before = len(ha.publishes)
    proc = subprocess.run(command + extra, cwd=workdir, env=env,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                          timeout=60)
    with ha.lock:
        produced = ha.publishes[before:]
    print("%-8s exit=%d  published %d message(s) %s"
          % (name, proc.returncode, len(produced), extra))
    if proc.returncode != 0:
        print("         output: %s" % proc.stdout.strip()[:500])
    return produced


def compare(label, left, right):
    ok = True
    if len(left) != len(right):
        print("  %-46s MISMATCH  %d vs %d messages" % (label, len(left), len(right)))
        for side, seq in (("python", left), ("rust", right)):
            for i, (topic, payload, retain) in enumerate(seq):
                print("      %s #%d topic=%s retain=%s\n              %s"
                      % (side, i, topic, retain, payload))
        return False
    for i, (a, b) in enumerate(zip(left, right)):
        if a == b:
            print("  %-46s MATCH     %s" % (label if i == 0 else "", a[0]))
        else:
            ok = False
            print("  %-46s DIFFER    #%d" % (label, i))
            print("      python: topic=%r retain=%r\n              payload=%s" % (a[0], a[2], a[1]))
            print("      rust  : topic=%r retain=%r\n              payload=%s" % (b[0], b[2], b[1]))
    return ok


def main():
    for path, what in ((PY_POLLER, "python poller"), (RS_POLLER, "rust poller"), (STUB, "stub")):
        if not os.path.exists(path):
            print("missing %s: %s" % (what, path))
            return 2

    workdir = tempfile.mkdtemp(prefix="deyepv-differential-")
    procs = []
    try:
        py_stub_port, rs_stub_port = free_port(), free_port()
        procs.append(start_stub(py_stub_port))
        procs.append(start_stub(rs_stub_port))
        if not wait_port(py_stub_port) or not wait_port(rs_stub_port):
            print("a stub logger never came up")
            return 2

        py_ha, rs_ha = FakeHa(), FakeHa()
        py_poller = patched_python_poller(py_stub_port, workdir)

        print("python poller : %s" % PY_POLLER)
        print("              : (patched copy: host 192.168.178.33 -> 127.0.0.1, port 8899 -> %d)" % py_stub_port)
        print("rust poller   : %s" % RS_POLLER)
        print("-" * 78)

        failures = 0

        # 1. one poll: state + availability
        py_once = run_side("python", [python_interpreter(), py_poller], py_ha, py_stub_port, workdir, ["--once"])
        rs_once = run_side("rust", [RS_POLLER, "--host", "127.0.0.1", "--port", str(rs_stub_port),
                                    "--log-csv", os.path.join(workdir, "unused.csv")],
                           rs_ha, rs_stub_port, workdir, ["--once"])
        print("1) one poll (state + availability)")
        if not compare("state and availability", py_once, rs_once):
            failures += 1

        # 2. discovery: five entity configs + availability
        py_disc = run_side("python", [python_interpreter(), py_poller], py_ha, py_stub_port, workdir, ["--discovery"])
        rs_disc = run_side("rust", [RS_POLLER, "--host", "127.0.0.1", "--port", str(rs_stub_port),
                                    "--log-csv", os.path.join(workdir, "unused.csv")],
                           rs_ha, rs_stub_port, workdir, ["--discovery"])
        print("2) discovery (five configs + availability)")
        if not compare("discovery payloads", py_disc, rs_disc):
            failures += 1

        print("-" * 78)
        if failures:
            print("%d comparison(s) differ" % failures)
            return 1
        print("all published messages identical")
        return 0
    finally:
        for p in procs:
            p.terminate()
        time.sleep(0.2)
        for p in procs:
            p.kill()
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
