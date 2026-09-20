#!/usr/bin/env python3
"""Run the Python poller once against our stub logger, with its output shown.

The differential test depends on the stub being good enough for the *reference*
implementation to read through it. When it is not, this shows exactly why instead
of a bare "the two sides published different numbers of messages".
"""

import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import differential_test as dt  # noqa: E402


def main():
    workdir = tempfile.mkdtemp(prefix="deyepv-stubcheck-")
    stub_port = dt.free_port()
    stub = dt.start_stub(stub_port)
    try:
        if not dt.wait_port(stub_port):
            print("stub never came up")
            return 2
        ha = dt.FakeHa()
        poller = dt.patched_python_poller(stub_port, workdir)

        env = dict(os.environ)
        env["HASS_URL"] = "http://127.0.0.1:%d" % ha.port
        env["HASS_TOKEN"] = "stub-check-token"
        env["PYTHONPATH"] = dt.HA_PROJECT

        print("stub on 127.0.0.1:%d, fake HA on 127.0.0.1:%d" % (stub_port, ha.port))
        print("-" * 70)
        proc = subprocess.run([sys.executable, poller, "--once"], cwd=workdir,
                              env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              text=True, timeout=60)
        print(proc.stdout)
        print("-" * 70)
        print("exit code: %d" % proc.returncode)
        print("published: %s" % ha.publishes)
        return 0
    finally:
        stub.terminate()
        import shutil
        import time
        time.sleep(0.2)
        stub.kill()
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
