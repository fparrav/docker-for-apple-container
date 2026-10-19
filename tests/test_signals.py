"""Signal propagation through the shim (agent CLI contract, fake runtime).

A caller that terminates an attached ``docker run`` must not leave the
underlying ``container`` process behind.
"""
from __future__ import annotations

import os
import signal
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

TIER = "fake"

ROOT = Path(__file__).resolve().parents[1]
SHIM = ROOT / "bin" / "docker"

FAKE_CONTAINER = """#!/bin/sh
echo $$ > "$FAKE_PIDFILE"
exec sleep 60
"""


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _wait_for(predicate, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return predicate()


class SignalPropagationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        tmp = Path(self.tmp.name)
        self.pidfile = tmp / "child.pid"
        fake = tmp / "container"
        fake.write_text(FAKE_CONTAINER)
        fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
        self.env = {
            "PATH": os.environ.get("PATH", ""),
            "HOME": str(tmp),
            "CONTAINER_DOCKER_SHIM_CONTAINER": str(fake),
            "FAKE_PIDFILE": str(self.pidfile),
        }

    def _start_attached_run(self) -> tuple:
        shim = subprocess.Popen(
            [sys.executable, str(SHIM), "run", "alpine:3.20"],
            env=self.env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.addCleanup(self._reap, shim)
        self.assertTrue(
            _wait_for(lambda: self.pidfile.exists() and self.pidfile.read_text().strip(), 10),
            "the fake container never started",
        )
        return shim, int(self.pidfile.read_text().strip())

    def _reap(self, shim: subprocess.Popen) -> None:
        if shim.poll() is None:
            shim.kill()
            shim.wait()
        if self.pidfile.exists():
            try:
                os.kill(int(self.pidfile.read_text().strip()), signal.SIGKILL)
            except (ProcessLookupError, ValueError):
                pass

    def test_sigterm_ends_the_shim_and_the_container_process(self) -> None:
        shim, child = self._start_attached_run()
        shim.send_signal(signal.SIGTERM)
        self.assertTrue(_wait_for(lambda: shim.poll() is not None, 10), "the shim survived SIGTERM")
        self.assertTrue(
            _wait_for(lambda: not _alive(child), 10),
            "the container process survived SIGTERM sent to the shim",
        )

    def test_sigint_ends_the_shim_and_the_container_process(self) -> None:
        shim, child = self._start_attached_run()
        shim.send_signal(signal.SIGINT)
        self.assertTrue(_wait_for(lambda: shim.poll() is not None, 10), "the shim survived SIGINT")
        self.assertTrue(
            _wait_for(lambda: not _alive(child), 10),
            "the container process survived SIGINT sent to the shim",
        )


if __name__ == "__main__":
    unittest.main()
