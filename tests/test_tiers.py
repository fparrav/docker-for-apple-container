from __future__ import annotations

import json
import os
import re
import sys
import unittest
from pathlib import Path
from typing import Mapping
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from container_docker_shim.cli import SUPPORTED_CONTAINER_VERSION  # noqa: E402

import tiers  # noqa: E402

TIER = "unit"


class FakeProc:
    def __init__(
        self, returncode: int = 0, stdout: str = "", stderr: str = ""
    ) -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _runner(
    *,
    returncode: int = 0,
    stdout: str = "",
    stderr: str = "",
    raises: type[BaseException] | None = None,
):
    def run(args: list[str]) -> FakeProc:
        assert args == ["container", "system", "status"], args
        if raises is not None:
            raise raises("container")
        return FakeProc(returncode, stdout, stderr)

    return run


def _engine_runner(
    *,
    client: str = "/usr/bin/docker",
    returncode: int = 0,
    stdout: str = "",
    stderr: str = "",
    raises: type[BaseException] | None = None,
):
    """Fake runner for the conformance gate's ``<client> info`` probe."""

    def run(args: list[str]) -> FakeProc:
        assert args == [client, "info"], args
        if raises is not None:
            raise raises(client)
        return FakeProc(returncode, stdout, stderr)

    return run


def _status_output(version: str) -> str:
    return (
        "FIELD               VALUE\n"
        "status              running\n"
        f"client.version      {version}\n"
        f"server.version      {version}\n"
    )


class EnabledTiersTests(unittest.TestCase):
    def test_default_run_enables_no_opt_in_tier(self):
        enabled = tiers.enabled_tiers({})
        self.assertEqual(enabled, frozenset())
        for tier in tiers.OPT_IN_TIERS:
            self.assertNotIn(tier, enabled)

    def test_empty_variable_enables_nothing(self):
        self.assertEqual(tiers.enabled_tiers({"SHIM_TIERS": ""}), frozenset())

    def test_live_is_enabled(self):
        self.assertEqual(tiers.enabled_tiers({"SHIM_TIERS": "live"}), frozenset({"live"}))

    def test_comma_separated_list_with_whitespace(self):
        self.assertEqual(
            tiers.enabled_tiers({"SHIM_TIERS": "live, conformance,"}),
            frozenset({"live", "conformance"}),
        )

    def test_defaults_to_the_process_environment(self):
        with mock.patch.dict(os.environ, {"SHIM_TIERS": "live"}):
            self.assertEqual(tiers.enabled_tiers(), frozenset({"live"}))


class RuntimeSkipReasonTests(unittest.TestCase):
    def test_missing_runtime_produces_a_reason(self):
        reason = tiers.runtime_skip_reason(
            run=_runner(raises=FileNotFoundError)
        )
        self.assertIsNotNone(reason)
        self.assertIn("container", reason)
        self.assertIn("not available", reason)

    def test_failed_status_produces_a_reason(self):
        reason = tiers.runtime_skip_reason(
            run=_runner(returncode=1, stderr="apiserver down")
        )
        self.assertIsNotNone(reason)
        self.assertIn("exited 1", reason)
        self.assertIn("apiserver down", reason)

    def test_version_mismatch_produces_a_reason(self):
        reason = tiers.runtime_skip_reason(
            run=_runner(stdout=_status_output("1.6.0"))
        )
        self.assertIsNotNone(reason)
        self.assertIn("1.6.0", reason)
        self.assertIn(SUPPORTED_CONTAINER_VERSION, reason)

    def test_supported_running_runtime_is_usable(self):
        reason = tiers.runtime_skip_reason(
            run=_runner(stdout=_status_output(SUPPORTED_CONTAINER_VERSION))
        )
        self.assertIsNone(reason)

    def test_status_without_a_version_line_produces_a_reason(self):
        reason = tiers.runtime_skip_reason(
            run=_runner(stdout="status              running\n")
        )
        self.assertIsNotNone(reason)
        self.assertIn("no version", reason)


class EngineSkipReasonTests(unittest.TestCase):
    def test_reachable_engine_is_usable(self):
        with mock.patch.dict(
            os.environ, {tiers.CONFORMANCE_DOCKER_ENV: "/usr/bin/docker"}
        ):
            reason = tiers.engine_skip_reason(run=_engine_runner())
        self.assertIsNone(reason)

    def test_client_not_set_produces_a_reason(self):
        env = {
            key: value
            for key, value in os.environ.items()
            if key != tiers.CONFORMANCE_DOCKER_ENV
        }
        with mock.patch.dict(os.environ, env, clear=True):
            reason = tiers.engine_skip_reason(run=_engine_runner())
        self.assertIsNotNone(reason)
        self.assertIn(tiers.CONFORMANCE_DOCKER_ENV, reason)

    def test_failed_info_produces_a_reason(self):
        with mock.patch.dict(
            os.environ, {tiers.CONFORMANCE_DOCKER_ENV: "/usr/bin/docker"}
        ):
            reason = tiers.engine_skip_reason(
                run=_engine_runner(
                    returncode=1,
                    stderr="Cannot connect to the Docker daemon",
                )
            )
        self.assertIsNotNone(reason)
        self.assertIn("exited 1", reason)
        self.assertIn("Cannot connect", reason)

    def test_unexecutable_client_produces_a_reason(self):
        with mock.patch.dict(
            os.environ, {tiers.CONFORMANCE_DOCKER_ENV: "/no/such/docker"}
        ):
            reason = tiers.engine_skip_reason(
                run=_engine_runner(
                    client="/no/such/docker", raises=FileNotFoundError
                )
            )
        self.assertIsNotNone(reason)
        self.assertIn("not available", reason)

    def test_never_probes_the_apple_runtime(self):
        """The probe is the client's ``info``; no ``container`` argv is issued."""
        runner = RecordingRunner({("/usr/bin/docker", "info"): FakeProc(0)})
        with mock.patch.dict(
            os.environ, {tiers.CONFORMANCE_DOCKER_ENV: "/usr/bin/docker"}
        ):
            reason = tiers.engine_skip_reason(run=runner)
        self.assertIsNone(reason)
        self.assertEqual(runner.commands, [["/usr/bin/docker", "info"]])


class TierDecoratorTests(unittest.TestCase):
    def test_live_disabled_is_skipped_with_a_reason(self):
        @tiers.live_tier()
        def target() -> str:
            return "ran"

        with mock.patch.dict(os.environ, {"SHIM_TIERS": ""}):
            with self.assertRaises(unittest.SkipTest) as ctx:
                target()
        self.assertIn("live tier disabled", str(ctx.exception))
        self.assertIn("SHIM_TIERS", str(ctx.exception))

    def test_live_enabled_but_runtime_missing_is_skipped(self):
        @tiers.live_tier(run=_runner(raises=FileNotFoundError))
        def target() -> str:
            return "ran"

        with mock.patch.dict(os.environ, {"SHIM_TIERS": "live"}):
            with self.assertRaises(unittest.SkipTest) as ctx:
                target()
        self.assertIn("live tier cannot run", str(ctx.exception))
        self.assertIn("not available", str(ctx.exception))

    def test_live_enabled_but_version_mismatch_is_skipped(self):
        @tiers.live_tier(run=_runner(stdout=_status_output("1.6.0")))
        def target() -> str:
            return "ran"

        with mock.patch.dict(os.environ, {"SHIM_TIERS": "live"}):
            with self.assertRaises(unittest.SkipTest) as ctx:
                target()
        self.assertIn("live tier cannot run", str(ctx.exception))
        self.assertIn("1.6.0", str(ctx.exception))

    def test_live_enabled_and_runtime_ok_runs_the_test(self):
        @tiers.live_tier(
            run=_runner(stdout=_status_output(SUPPORTED_CONTAINER_VERSION))
        )
        def target() -> str:
            return "ran"

        with mock.patch.dict(os.environ, {"SHIM_TIERS": "live"}):
            self.assertEqual(target(), "ran")

    def test_conformance_disabled_is_skipped(self):
        @tiers.conformance_tier()
        def target() -> str:
            return "ran"

        with mock.patch.dict(os.environ, {"SHIM_TIERS": ""}):
            with self.assertRaises(unittest.SkipTest) as ctx:
                target()
        self.assertIn("conformance tier disabled", str(ctx.exception))

    def test_conformance_without_docker_client_is_skipped(self):
        @tiers.conformance_tier()
        def target() -> str:
            return "ran"

        env = {"SHIM_TIERS": "conformance", "SHIM_CONFORMANCE_DOCKER": ""}
        with mock.patch.dict(os.environ, env):
            with self.assertRaises(unittest.SkipTest) as ctx:
                target()
        self.assertIn("conformance tier cannot run", str(ctx.exception))
        self.assertIn("SHIM_CONFORMANCE_DOCKER", str(ctx.exception))

    def test_conformance_enabled_with_docker_client_runs_the_test(self):
        # The fake runner only accepts the client's ``info`` probe: a gate
        # that also probed the Apple runtime would fail the assertion.
        run = _engine_runner()

        @tiers.conformance_tier(run)
        def target() -> str:
            return "ran"

        env = {"SHIM_TIERS": "conformance", "SHIM_CONFORMANCE_DOCKER": "/usr/bin/docker"}
        with mock.patch.dict(os.environ, env):
            self.assertEqual(target(), "ran")

    def test_conformance_with_unreachable_engine_is_skipped(self):
        run = _engine_runner(
            returncode=1, stderr="Cannot connect to the Docker daemon"
        )

        @tiers.conformance_tier(run)
        def target() -> str:
            return "ran"

        env = {"SHIM_TIERS": "conformance", "SHIM_CONFORMANCE_DOCKER": "/usr/bin/docker"}
        with mock.patch.dict(os.environ, env):
            with self.assertRaises(unittest.SkipTest) as ctx:
                target()
        self.assertIn("conformance tier cannot run", str(ctx.exception))
        self.assertIn("exited 1", str(ctx.exception))

    def test_conformance_with_unexecutable_client_is_skipped(self):
        run = _engine_runner(client="/no/such/docker", raises=FileNotFoundError)

        @tiers.conformance_tier(run)
        def target() -> str:
            return "ran"

        env = {"SHIM_TIERS": "conformance", "SHIM_CONFORMANCE_DOCKER": "/no/such/docker"}
        with mock.patch.dict(os.environ, env):
            with self.assertRaises(unittest.SkipTest) as ctx:
                target()
        self.assertIn("conformance tier cannot run", str(ctx.exception))
        self.assertIn("not available", str(ctx.exception))

    def test_conformance_gate_never_probes_the_apple_runtime(self):
        runner = RecordingRunner({("/usr/bin/docker", "info"): FakeProc(0)})

        @tiers.conformance_tier(runner)
        def target() -> str:
            return "ran"

        env = {"SHIM_TIERS": "conformance", "SHIM_CONFORMANCE_DOCKER": "/usr/bin/docker"}
        with mock.patch.dict(os.environ, env):
            self.assertEqual(target(), "ran")
        self.assertEqual(runner.commands, [["/usr/bin/docker", "info"]])


class DisposableNameTests(unittest.TestCase):
    def test_name_carries_the_shimtest_prefix(self):
        name = tiers.disposable_name("sec")
        self.assertTrue(name.startswith("shimtest-sec-"))

    def test_name_suffix_is_eight_hex_chars(self):
        name = tiers.disposable_name("cmp")
        suffix = name.removeprefix("shimtest-cmp-")
        self.assertRegex(suffix, r"^[0-9a-f]{8}$")

    def test_names_are_unique(self):
        self.assertNotEqual(
            tiers.disposable_name("sec"), tiers.disposable_name("sec")
        )


class RecordingRunner:
    """Fake runner that records every argv and replays canned output.

    Any command without a canned entry succeeds with empty output, which is
    what the assertions below rely on: recorded commands are the evidence.
    """

    def __init__(
        self, canned: Mapping[tuple[str, ...], FakeProc] | None = None
    ) -> None:
        self.commands: list[list[str]] = []
        self._canned = dict(canned or {})

    def __call__(self, args: list[str]) -> FakeProc:
        self.commands.append(args)
        return self._canned.get(tuple(args), FakeProc())


def _listing(*entries: object) -> str:
    return json.dumps(list(entries))


def _container(name: str, *, config_only: bool = False) -> dict:
    """One entry of a real ``container ls --all --format json`` array."""
    entry: dict = {"configuration": {"id": name}}
    if not config_only:
        entry["id"] = name
    return entry


def _network(name: str) -> dict:
    """One entry of a real ``container network list --format json`` array."""
    return {"id": name, "configuration": {"name": name}}


CONTAINER_LIST = ["container", "ls", "--all", "--format", "json"]
VOLUME_LIST = ["container", "volume", "list", "--format", "json"]
NETWORK_LIST = ["container", "network", "list", "--format", "json"]


class CleanupByPrefixTests(unittest.TestCase):
    def _mixed_scenario_runner(self) -> RecordingRunner:
        prefix = "shimtest-cmp-"
        return RecordingRunner(
            {
                tuple(CONTAINER_LIST): FakeProc(
                    0,
                    _listing(
                        _container(f"{prefix}a1"),
                        _container(f"{prefix}a2", config_only=True),
                        _container("buildkit"),
                        {"foo": "bar"},
                    ),
                ),
                tuple(VOLUME_LIST): FakeProc(
                    0, _listing({"name": f"{prefix}v1"}, {"name": "data"})
                ),
                tuple(NETWORK_LIST): FakeProc(
                    0, _listing(_network(f"{prefix}n1"), _network("default"))
                ),
            }
        )

    def test_refuses_a_prefix_outside_shimtest(self):
        for prefix in ("", "shimtest", "buildkit", "shim-test-", "SHIMTEST-"):
            runner = RecordingRunner()
            with self.assertRaises(ValueError):
                tiers.cleanup_by_prefix(prefix, runner)
            self.assertEqual(runner.commands, [])

    def test_removes_only_the_prefixed_objects(self):
        runner = self._mixed_scenario_runner()
        tiers.cleanup_by_prefix("shimtest-cmp-", runner)
        self.assertEqual(
            runner.commands,
            [
                CONTAINER_LIST,
                ["container", "stop", "shimtest-cmp-a1"],
                ["container", "rm", "shimtest-cmp-a1"],
                ["container", "stop", "shimtest-cmp-a2"],
                ["container", "rm", "shimtest-cmp-a2"],
                VOLUME_LIST,
                ["container", "volume", "rm", "shimtest-cmp-v1"],
                NETWORK_LIST,
                ["container", "network", "rm", "shimtest-cmp-n1"],
            ],
        )

    def test_never_issues_a_prune(self):
        runner = self._mixed_scenario_runner()
        tiers.cleanup_by_prefix("shimtest-cmp-", runner)
        for command in runner.commands:
            self.assertNotIn("prune", command)

    def test_foreign_objects_are_untouched(self):
        runner = self._mixed_scenario_runner()
        tiers.cleanup_by_prefix("shimtest-cmp-", runner)
        for name in ("buildkit", "data", "default"):
            for command in runner.commands:
                self.assertNotIn(name, command[1:])

    def test_empty_lists_remove_nothing(self):
        runner = RecordingRunner(
            {
                tuple(CONTAINER_LIST): FakeProc(0, "[]"),
                tuple(VOLUME_LIST): FakeProc(0, "[]"),
                tuple(NETWORK_LIST): FakeProc(0, ""),
            }
        )
        tiers.cleanup_by_prefix("shimtest-cmp-", runner)
        self.assertEqual(
            runner.commands, [CONTAINER_LIST, VOLUME_LIST, NETWORK_LIST]
        )

    def test_listing_failure_raises_and_removes_nothing(self):
        runner = RecordingRunner(
            {tuple(CONTAINER_LIST): FakeProc(1, stderr="apiserver down")}
        )
        with self.assertRaises(RuntimeError) as ctx:
            tiers.cleanup_by_prefix("shimtest-cmp-", runner)
        self.assertIn("exited 1", str(ctx.exception))
        self.assertEqual(runner.commands, [CONTAINER_LIST])

    def test_invalid_json_raises(self):
        runner = RecordingRunner(
            {tuple(VOLUME_LIST): FakeProc(0, "not json")}
        )
        with self.assertRaises(RuntimeError) as ctx:
            tiers.cleanup_by_prefix("shimtest-cmp-", runner)
        self.assertIn("invalid JSON", str(ctx.exception))

    def test_stop_failure_does_not_block_removal(self):
        runner = RecordingRunner(
            {
                tuple(CONTAINER_LIST): FakeProc(0, _listing(_container("shimtest-cmp-a1"))),
                ("container", "stop", "shimtest-cmp-a1"): FakeProc(
                    1, stderr="not running"
                ),
            }
        )
        tiers.cleanup_by_prefix("shimtest-cmp-", runner)
        self.assertIn(["container", "stop", "shimtest-cmp-a1"], runner.commands)
        self.assertIn(["container", "rm", "shimtest-cmp-a1"], runner.commands)

    def test_removal_failure_raises(self):
        runner = RecordingRunner(
            {
                tuple(CONTAINER_LIST): FakeProc(0, _listing(_container("shimtest-cmp-a1"))),
                ("container", "rm", "shimtest-cmp-a1"): FakeProc(
                    1, stderr="rm: boom"
                ),
            }
        )
        with self.assertRaises(RuntimeError) as ctx:
            tiers.cleanup_by_prefix("shimtest-cmp-", runner)
        self.assertIn("shimtest-cmp-a1", str(ctx.exception))
        self.assertIn("rm: boom", str(ctx.exception))


class TierDeclarationTests(unittest.TestCase):
    """Every ``tests/test_*.py`` module must declare a valid ``TIER``.

    The check is import-free: it reads each file's text and looks for a
    ``TIER = "unit" | "fake" | "live" | "conformance"`` line, so a module
    that lacks the constant (or misspells it) fails without being imported.
    """

    TIER_PATTERN = re.compile(r'^TIER = "(unit|fake|live|conformance)"$')

    def test_every_test_module_declares_a_valid_tier(self):
        tests_dir = Path(__file__).resolve().parent
        modules = sorted(tests_dir.glob("test_*.py"))
        self.assertGreater(len(modules), 0, "no tests/test_*.py found")
        for module in modules:
            with self.subTest(module=module.name):
                lines = module.read_text().splitlines()
                self.assertTrue(
                    any(self.TIER_PATTERN.match(line) for line in lines),
                    f"{module.name} does not declare TIER",
                )


if __name__ == "__main__":
    unittest.main()
