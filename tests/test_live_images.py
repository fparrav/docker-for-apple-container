"""Live image tests (opt-in "live" tier) for `docker build`, registry round-trips and platform runs.

These tests run the real shim (``bin/docker``) against the real Apple
``container`` runtime and prove what the build translation produces in real
images: that a ``--build-arg`` value reaches the image filesystem, that a CLI
``--label`` overrides the Dockerfile ``LABEL`` as reported by
``image inspect``, that a built tag pushed to and pulled back from a
disposable ``registry:2`` container keeps the same image digest while the
registry stores an OCI index manifest, and that compat fixture F
(``f-arm64`` under ``FIXTURES_DIR``) built for ``linux/arm64`` runs a
container whose ``uname -m`` prints ``aarch64``.

Every test method is gated with ``tiers.live_tier``: it is skipped unless
``SHIM_TIERS`` contains ``live`` and the supported ``container`` runtime is
usable, and each test skips additionally (``require_images``) when
``alpine:3.20`` (and ``registry:2`` for the round trip) is not already
available locally (never pulled).

Every disposable object is named through ``tiers.disposable_name`` and
removed in ``addCleanup`` by exact name only: images are tagged
``shimtest-img-<short uuid>:1`` and removed with ``rmi -f``, the registry
container ``shimtest-reg-<short uuid>`` with ``rm -f``; the suite never
prunes or matches by pattern.
"""

from __future__ import annotations

import json
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

import tiers  # noqa: E402

from test_live_fixtures import (  # noqa: E402
    FIXTURES_DIR,
    SHIM,
    _container,
    _image_available,
    _run,
    copy_fixture,
)

TIER = "live"

FIXTURE_F = "f-arm64"
FIXTURE_G = "g-amd64"
BASE_IMAGE = "alpine:3.20"
REGISTRY_IMAGE = "registry:2"
BUILD_TIMEOUT_SECONDS = 300
REGISTRY_WAIT_SECONDS = 30
OCI_INDEX_MEDIA_TYPE = "application/vnd.oci.image.index.v1+json"

DOCKERFILE = (
    "FROM alpine:3.20\n"
    "ARG GREETING=none\n"
    'RUN echo "$GREETING" > /greeting.txt\n'
    "LABEL shimtest.label=on\n"
)

PUSH_DOCKERFILE = (
    "FROM alpine:3.20\n"
    "RUN echo pushed > /marker\n"
)


class LiveImageTests(unittest.TestCase):
    # -- helpers ---------------------------------------------------------

    def require_images(self, *references: str) -> None:
        """Skip the test unless every ``repository:tag`` is already local (never pulled)."""
        try:
            images = _run([_container(), "image", "ls"])
        except (OSError, subprocess.SubprocessError) as exc:
            self.skipTest(f"container image ls failed: {exc}")
        listing = images.stdout + images.stderr
        missing = [
            reference
            for reference in references
            if images.returncode != 0
            or not _image_available(listing, *reference.split(":", 1))
        ]
        if missing:
            self.skipTest(f"images {', '.join(missing)} not available locally")

    def _image_tag(self) -> str:
        """Tag of a disposable built image: ``shimtest-img-<short uuid>:1``."""
        return tiers.disposable_name("img") + ":1"

    def _remove_image(self, tag: str) -> None:
        """Cleanup: remove exactly this tag; errors are ignored, never prune."""
        try:
            _run([str(SHIM), "rmi", "-f", tag])
        except (OSError, subprocess.SubprocessError):
            pass

    def _remove_registry(self, reg_name: str) -> None:
        """Cleanup: force-remove exactly this registry container; errors ignored, never prune."""
        try:
            _run([str(SHIM), "rm", "-f", reg_name])
        except (OSError, subprocess.SubprocessError):
            pass

    def _free_port(self) -> int:
        """A free 127.0.0.1 port: bind to port 0, take the OS choice, release it."""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            return sock.getsockname()[1]

    def _wait_for_registry(self, port: int, wait_seconds: float = REGISTRY_WAIT_SECONDS) -> None:
        """Poll the registry's ``/v2/`` endpoint until it answers HTTP 200, or fail after the deadline."""
        url = f"http://127.0.0.1:{port}/v2/"
        deadline = time.monotonic() + wait_seconds
        last_error = "not attempted"
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(url, timeout=5) as response:
                    if response.code == 200:
                        return
                    last_error = f"HTTP {response.code} from {url}"
            except OSError as exc:
                last_error = f"{url} unreachable: {exc}"
            time.sleep(1)
        self.fail(f"{url} did not answer HTTP 200 within {wait_seconds:.0f}s: {last_error}")

    def _image_id(self, reference: str) -> str:
        """The image digest, read from ``image inspect --format {{.Id}}``."""
        proc = _run([str(SHIM), "image", "inspect", "--format", "{{.Id}}", reference])
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        return proc.stdout.strip()

    def _registry_manifest_media_type(self, port: int, name: str) -> str:
        """The mediaType of the stored manifest, requested as an OCI index."""
        url = f"http://127.0.0.1:{port}/v2/shimtest/{name}/manifests/1"
        request = urllib.request.Request(url, headers={"Accept": OCI_INDEX_MEDIA_TYPE})
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return str(json.load(response)["mediaType"])
        except (OSError, ValueError) as exc:
            self.fail(f"GET {url} failed: {exc}")

    # -- tests -----------------------------------------------------------

    @tiers.live_tier()
    def test_build_arg_reaches_image_and_cli_label_wins(self) -> None:
        self.require_images(BASE_IMAGE)
        tag = self._image_tag()
        self.addCleanup(self._remove_image, tag)

        with tempfile.TemporaryDirectory(prefix="shimtest-img-") as work:
            workdir = Path(work)
            (workdir / "Dockerfile").write_text(DOCKERFILE)

            build = _run(
                [
                    str(SHIM),
                    "build",
                    "--build-arg",
                    "GREETING=hello-live",
                    "--label",
                    "shimtest.label=cli",
                    "-t",
                    tag,
                    str(workdir),
                ],
                timeout=BUILD_TIMEOUT_SECONDS,
            )
            self.assertEqual(build.returncode, 0, build.stdout + build.stderr)

            # The build arg must reach the image filesystem.
            run = _run([str(SHIM), "run", "--rm", tag, "cat", "/greeting.txt"])
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
            self.assertIn("hello-live", run.stdout)

            # The CLI --label must win over the Dockerfile LABEL.
            inspect = _run(
                [str(SHIM), "image", "inspect", "--format", "{{json .Config.Labels}}", tag]
            )
            self.assertEqual(inspect.returncode, 0, inspect.stdout + inspect.stderr)
            labels = json.loads(inspect.stdout)
            self.assertIsInstance(labels, dict)
            self.assertEqual(labels.get("shimtest.label"), "cli")

    @tiers.live_tier()
    def test_fixture_f_arm64_image_reports_aarch64(self) -> None:
        self.require_images(BASE_IMAGE)
        tag = self._image_tag()
        self.addCleanup(self._remove_image, tag)

        workdir = copy_fixture(FIXTURE_F)
        self.addCleanup(shutil.rmtree, workdir, ignore_errors=True)

        build = _run(
            [str(SHIM), "build", "--platform", "linux/arm64", "-t", tag, str(workdir)],
            timeout=BUILD_TIMEOUT_SECONDS,
        )
        self.assertEqual(build.returncode, 0, build.stdout + build.stderr)

        # The fixture's CMD prints `uname -m`; the arm64 variant must say aarch64.
        run = _run([str(SHIM), "run", "--rm", "--platform", "linux/arm64", tag])
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertEqual(run.stdout.strip(), "aarch64")

    @tiers.live_tier()
    def test_fixture_g_amd64_image_runs_only_with_an_explicit_platform(self) -> None:
        self.require_images(BASE_IMAGE)
        tag = self._image_tag()
        self.addCleanup(self._remove_image, tag)

        workdir = copy_fixture(FIXTURE_G)
        self.addCleanup(shutil.rmtree, workdir, ignore_errors=True)

        build = _run(
            [str(SHIM), "build", "--platform", "linux/amd64", "-t", tag, str(workdir)],
            timeout=BUILD_TIMEOUT_SECONDS,
        )
        self.assertEqual(build.returncode, 0, build.stdout + build.stderr)

        # Findings 1.1: the amd64 variant runs under emulation when the
        # platform is explicit...
        run = _run([str(SHIM), "run", "--rm", "--platform", "linux/amd64", tag])
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertEqual(run.stdout.strip(), "x86_64")

        # ...but, unlike Docker Desktop, an image with no host-platform variant
        # is not picked automatically: Apple container asks for linux/arm64
        # and fails. This difference is documented in the README.
        implicit = _run([str(SHIM), "run", "--rm", tag])
        self.assertNotEqual(implicit.returncode, 0, implicit.stdout + implicit.stderr)

    @tiers.live_tier()
    def test_push_pull_round_trip_keeps_digest_and_oci_index_manifest(self) -> None:
        self.require_images(BASE_IMAGE, REGISTRY_IMAGE)

        name = tiers.disposable_name("img")
        local_tag = f"{name}:1"
        port = self._free_port()
        reg_name = tiers.disposable_name("reg")
        remote_ref = f"localhost:{port}/shimtest/{name}:1"

        # Cleanups run in reverse registration order: the registry container
        # is force-removed first, then the local tags by exact name.
        self.addCleanup(self._remove_image, remote_ref)
        self.addCleanup(self._remove_image, local_tag)
        self.addCleanup(self._remove_registry, reg_name)

        registry = _run(
            [
                str(SHIM),
                "run",
                "-d",
                "--name",
                reg_name,
                "-p",
                f"127.0.0.1:{port}:5000",
                REGISTRY_IMAGE,
            ]
        )
        self.assertEqual(registry.returncode, 0, registry.stdout + registry.stderr)
        self._wait_for_registry(port)

        with tempfile.TemporaryDirectory(prefix="shimtest-img-") as work:
            workdir = Path(work)
            (workdir / "Dockerfile").write_text(PUSH_DOCKERFILE)

            build = _run(
                [str(SHIM), "build", "-t", local_tag, str(workdir)],
                timeout=BUILD_TIMEOUT_SECONDS,
            )
            self.assertEqual(build.returncode, 0, build.stdout + build.stderr)

            before = self._image_id(local_tag)

            # A plain-HTTP registry needs --scheme http; the shim forwards
            # image push/pull arguments to Apple container unchanged.
            tag = _run([str(SHIM), "image", "tag", local_tag, remote_ref])
            self.assertEqual(tag.returncode, 0, tag.stdout + tag.stderr)

            push = _run([str(SHIM), "image", "push", "--scheme", "http", remote_ref])
            self.assertEqual(push.returncode, 0, push.stdout + push.stderr)

            # Drop the local copy so the pull can only come from the registry.
            rmi = _run([str(SHIM), "rmi", remote_ref])
            self.assertEqual(rmi.returncode, 0, rmi.stdout + rmi.stderr)

            pull = _run([str(SHIM), "image", "pull", "--scheme", "http", remote_ref])
            self.assertEqual(pull.returncode, 0, pull.stdout + pull.stderr)

            after = self._image_id(remote_ref)
            self.assertNotEqual(before, "")
            self.assertEqual(after, before, "the pulled digest differs from the pushed one")

            media_type = self._registry_manifest_media_type(port, name)
            self.assertEqual(media_type, OCI_INDEX_MEDIA_TYPE)


if __name__ == "__main__":
    unittest.main()
