"""Live security tests (opt-in "live" tier) for the `docker run` controls.

These tests run the real shim (``bin/docker``) against the real Apple
``container`` runtime and prove the in-guest effect of each control. The whole
class is skipped unless:

* ``SHIM_TIERS`` contains ``live`` (comma-separated list),
* ``container system status`` exits 0 and reports a running apiserver at the
  supported version,
* the ``alpine:3.20`` image is already available locally (never pulled).

Every disposable container is named ``shimtest-sec-<short uuid>`` and removed
by exact name in ``addCleanup``; the suite never prunes or matches by pattern.
"""

from __future__ import annotations

import os
import subprocess
import sys
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from container_docker_shim.cli import SUPPORTED_CONTAINER_VERSION  # noqa: E402

SHIM = ROOT / "bin" / "docker"
NAME_PREFIX = "shimtest-sec-"
IMAGE = "alpine:3.20"
TIMEOUT_SECONDS = 120


def _run(args: list[str]) -> subprocess.CompletedProcess:
    """Run a subprocess with captured output; every live call uses the 120 s timeout."""
    return subprocess.run(args, capture_output=True, text=True, timeout=TIMEOUT_SECONDS)


class LiveSecurityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        tiers = {
            tier.strip()
            for tier in os.environ.get("SHIM_TIERS", "").split(",")
            if tier.strip()
        }
        if "live" not in tiers:
            raise unittest.SkipTest(
                "live tier disabled: set SHIM_TIERS=live to run live security tests"
            )
        try:
            status = _run(["container", "system", "status"])
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise unittest.SkipTest(f"container system status failed: {exc}") from exc
        output = (status.stdout + status.stderr).strip()
        if status.returncode != 0:
            raise unittest.SkipTest(
                f"container system status exited {status.returncode}: {output}"
            )
        if "running" not in output:
            raise unittest.SkipTest(f"container apiserver is not running: {output}")
        if SUPPORTED_CONTAINER_VERSION not in output:
            raise unittest.SkipTest(
                f"container system status does not report the supported "
                f"version {SUPPORTED_CONTAINER_VERSION}: {output}"
            )
        try:
            images = _run(["container", "image", "ls"])
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise unittest.SkipTest(f"container image ls failed: {exc}") from exc
        listing = images.stdout + images.stderr
        if images.returncode != 0 or "alpine" not in listing or "3.20" not in listing:
            raise unittest.SkipTest(f"image {IMAGE} not available locally")

    # -- helpers ---------------------------------------------------------

    def _name(self) -> str:
        return f"{NAME_PREFIX}{uuid.uuid4().hex[:8]}"

    def _run_shim(self, *args: str) -> subprocess.CompletedProcess:
        return _run([sys.executable, str(SHIM), *args])

    def _remove(self, name: str) -> None:
        """Cleanup: remove by exact name only; errors are ignored, never prune."""
        try:
            _run(["container", "rm", "-f", name])
        except (OSError, subprocess.TimeoutExpired):
            pass

    # -- tests -----------------------------------------------------------

    def test_cap_drop_all_leaves_no_effective_capabilities(self) -> None:
        name = self._name()
        self.addCleanup(self._remove, name)
        result = self._run_shim(
            "run",
            "--name", name,
            "--cap-drop", "ALL",
            IMAGE,
            "sh", "-c", "grep CapEff /proc/self/status",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("CapEff:\t0000000000000000", result.stdout)

    def test_tmpfs_options_are_applied_to_the_mount(self) -> None:
        name = self._name()
        self.addCleanup(self._remove, name)
        result = self._run_shim(
            "run",
            "--name", name,
            "--tmpfs", "/tmp:ro,noexec,size=64m",
            IMAGE,
            "sh", "-c", "grep ' /tmp ' /proc/mounts",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(result.stdout.strip(), "no /tmp mount line found")
        line = result.stdout.splitlines()[-1]
        self.assertIn("ro", line)
        self.assertIn("noexec", line)
        self.assertIn("size=65536k", line)

    def test_read_only_root_filesystem_rejects_writes(self) -> None:
        name = self._name()
        self.addCleanup(self._remove, name)
        result = self._run_shim(
            "run",
            "--name", name,
            "--read-only",
            IMAGE,
            "sh", "-c", "touch /rootfile",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Read-only file system", result.stdout + result.stderr)

    def test_user_maps_to_uid_1000(self) -> None:
        name = self._name()
        self.addCleanup(self._remove, name)
        result = self._run_shim(
            "run",
            "--name", name,
            "--user", "1000:1000",
            IMAGE,
            "id", "-u",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "1000")

    def test_privileged_is_refused_before_any_container_is_created(self) -> None:
        name = self._name()
        self.addCleanup(self._remove, name)
        result = self._run_shim(
            "run",
            "--name", name,
            "--privileged",
            IMAGE,
            "true",
        )
        self.assertEqual(result.returncode, 64, result.stderr)
        listing = _run(["container", "ls", "-a"])
        self.assertNotIn(name, listing.stdout + listing.stderr)


if __name__ == "__main__":
    unittest.main()
