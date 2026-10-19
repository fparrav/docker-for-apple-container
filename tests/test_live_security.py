"""Live security tests (opt-in "live" tier) for the `docker run` controls.

These tests run the real shim (``bin/docker``) against the real Apple
``container`` runtime and prove the in-guest effect of each control. Every
test method is gated with ``tiers.live_tier``: it is skipped unless
``SHIM_TIERS`` contains ``live`` and the supported ``container`` runtime is
usable, and the class skips additionally when the ``alpine:3.20`` image is
not already available locally (never pulled).

Every disposable container is named ``shimtest-sec-<short uuid>`` through
``tiers.disposable_name`` and removed by exact name in ``addCleanup``; the
suite never prunes or matches by pattern.
"""

from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

import tiers  # noqa: E402

TIER = "live"

SHIM = ROOT / "bin" / "docker"
IMAGE = "alpine:3.20"
TIMEOUT_SECONDS = 120


def _run(args: list[str]) -> subprocess.CompletedProcess:
    """Run a subprocess with captured output; every live call uses the 120 s timeout."""
    return subprocess.run(args, capture_output=True, text=True, timeout=TIMEOUT_SECONDS)


class LiveSecurityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if "live" not in tiers.enabled_tiers():
            return
        try:
            images = _run(["container", "image", "ls"])
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise unittest.SkipTest(f"container image ls failed: {exc}") from exc
        listing = images.stdout + images.stderr
        if images.returncode != 0 or "alpine" not in listing or "3.20" not in listing:
            raise unittest.SkipTest(f"image {IMAGE} not available locally")

    # -- helpers ---------------------------------------------------------

    def _name(self) -> str:
        return tiers.disposable_name("sec")

    def _run_shim(self, *args: str) -> subprocess.CompletedProcess:
        return _run([sys.executable, str(SHIM), *args])

    def _remove(self, name: str) -> None:
        """Cleanup: remove by exact name only; errors are ignored, never prune."""
        try:
            _run(["container", "rm", "-f", name])
        except (OSError, subprocess.TimeoutExpired):
            pass

    # -- tests -----------------------------------------------------------

    @tiers.live_tier()
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

    @tiers.live_tier()
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

    @tiers.live_tier()
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

    @tiers.live_tier()
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

    @tiers.live_tier()
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
