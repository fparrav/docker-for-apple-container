"""Unit tests for ``scripts/verify_wheel.py`` (design D7).

The pure checks run on synthetic archives. The install checks build a tiny but
valid wheel by hand (a stand-in ``container_docker_shim`` package with the same
entry point) and install it into a real virtual environment with ``--no-index``,
so no network, no Apple runtime and no real build backend are needed. The
environment is created once for the class and removed afterwards.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import os
import platform
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]

TIER = "unit"


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


verifier = _load("verify_wheel", "verify_wheel.py")

DIST_INFO = "docker_for_apple_container_fork-1.2.3.dist-info"
ENTRY_POINTS = "[console_scripts]\ndocker = container_docker_shim.cli:main\n"

FAKE_CLI = '''\
import os
import sys
from importlib.metadata import version


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "version":
        if not os.environ.get("CONTAINER_DOCKER_SHIM_CONTAINER"):
            sys.stderr.write("docker-for-apple-container: Apple container CLI not found: set it\\n")
            return 1
        print("Shim: " + version("docker-for-apple-container-fork"))
        return 0
    sys.stderr.write("docker-for-apple-container: Apple container CLI not found: set it\\n")
    return 1
'''


def write_wheel(directory: Path, *, files: dict | None = None, entry_points: str | None = ENTRY_POINTS,
                version: str = "1.2.3", name: str | None = None) -> Path:
    """A minimal valid wheel; ``files`` overrides or adds archive members."""
    dist_info = f"docker_for_apple_container_fork-{version}.dist-info"
    members = {
        "container_docker_shim/__init__.py": "",
        "container_docker_shim/cli.py": FAKE_CLI,
        f"{dist_info}/METADATA": (
            f"Metadata-Version: 2.1\nName: docker-for-apple-container-fork\nVersion: {version}\n"
        ),
        f"{dist_info}/WHEEL": (
            "Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n"
        ),
    }
    if entry_points is not None:
        members[f"{dist_info}/entry_points.txt"] = entry_points
    members.update(files or {})
    members = {key: value for key, value in members.items() if value is not None}
    members[f"{dist_info}/RECORD"] = "".join(f"{path},,\n" for path in [*members, f"{dist_info}/RECORD"])
    path = directory / (name or f"docker_for_apple_container_fork-{version}-py3-none-any.whl")
    with zipfile.ZipFile(path, "w") as archive:
        for member, text in members.items():
            archive.writestr(member, text)
    return path


class FindWheelTests(unittest.TestCase):
    def test_one_wheel_is_returned(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wheel = write_wheel(Path(tmp))
            # resolve both sides: the temporary directory may sit behind a symlink (macOS /var)
            self.assertEqual(verifier.find_wheel(Path(tmp)), wheel.resolve())

    def test_no_wheel_and_a_missing_directory_fail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(verifier.CheckError, "no wheel found"):
                verifier.find_wheel(Path(tmp))
            with self.assertRaisesRegex(verifier.CheckError, "no wheel found"):
                verifier.find_wheel(Path(tmp) / "missing")

    def test_two_wheels_fail_naming_both(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            write_wheel(Path(tmp), version="1.2.3")
            write_wheel(Path(tmp), version="1.2.4")
            with self.assertRaisesRegex(verifier.CheckError, r"found 2.*1\.2\.3.*1\.2\.4"):
                verifier.find_wheel(Path(tmp))


class ContentsTests(unittest.TestCase):
    def check(self, **kwargs) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            verifier.check_contents(write_wheel(Path(tmp), **kwargs))

    def test_a_complete_wheel_passes(self) -> None:
        self.check()

    def test_missing_cli_module_fails(self) -> None:
        with self.assertRaisesRegex(verifier.CheckError, "lacks container_docker_shim/cli.py"):
            self.check(files={"container_docker_shim/cli.py": None})

    def test_missing_package_init_fails(self) -> None:
        with self.assertRaisesRegex(verifier.CheckError, "lacks container_docker_shim/__init__.py"):
            self.check(files={"container_docker_shim/__init__.py": None})

    def test_missing_entry_points_file_fails(self) -> None:
        with self.assertRaisesRegex(verifier.CheckError, "no entry_points.txt"):
            self.check(entry_points=None)

    def test_missing_docker_script_fails(self) -> None:
        with self.assertRaisesRegex(verifier.CheckError, "does not declare the console script"):
            self.check(entry_points="[console_scripts]\nother = container_docker_shim.cli:main\n")

    def test_wrong_target_fails_naming_what_was_found(self) -> None:
        with self.assertRaisesRegex(verifier.CheckError, "container_docker_shim.cli:other"):
            self.check(entry_points="[console_scripts]\ndocker = container_docker_shim.cli:other\n")

    def test_a_file_that_is_not_a_zip_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "broken-1.0-py3-none-any.whl"
            path.write_bytes(b"not a zip")
            with self.assertRaisesRegex(verifier.CheckError, "not a readable wheel"):
                verifier.check_contents(path)


class InstalledWheelTests(unittest.TestCase):
    """One real virtual environment, shared by the tests of this class."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        cls.work = Path(cls._tmp.name)
        cls.wheel = write_wheel(cls.work)
        cls.env_dir = cls.work / "venv"
        verifier.install(cls.wheel, cls.env_dir, cls.work)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def test_import_comes_from_the_environment(self) -> None:
        verifier.check_import_location(self.env_dir, self.work)

    def test_version_reports_the_installed_shim_version(self) -> None:
        self.assertEqual(verifier.check_version(self.env_dir, self.work, "1.2.3"), "1.2.3")
        self.assertEqual(verifier.check_version(self.env_dir, self.work, None), "1.2.3")

    def test_version_mismatch_names_both_versions(self) -> None:
        with self.assertRaisesRegex(verifier.CheckError, "reports shim 1.2.3, expected 9.9.9"):
            verifier.check_version(self.env_dir, self.work, "9.9.9")

    def test_runtime_absent_exits_1_with_the_message(self) -> None:
        verifier.check_runtime_absent(self.env_dir, self.work)

    def test_outer_environment_cannot_leak_into_the_checks(self) -> None:
        leaked = {"CONTAINER_DOCKER_SHIM_CONTAINER": "/bin/true", "PYTHONPATH": str(ROOT / "src")}
        with mock.patch.dict(os.environ, leaked):
            verifier.check_runtime_absent(self.env_dir, self.work)
            verifier.check_import_location(self.env_dir, self.work)

    def test_a_wheel_reporting_the_source_version_fails(self) -> None:
        source = write_wheel(self.work, version="0+source")
        env_dir = self.work / "venv-source"
        verifier.install(source, env_dir, self.work)
        with self.assertRaisesRegex(verifier.CheckError, "carries no version metadata"):
            verifier.check_version(env_dir, self.work, None)

    def test_verify_runs_every_check_and_cleans_up(self) -> None:
        with tempfile.TemporaryDirectory() as dist:
            write_wheel(Path(dist))
            before = {p for p in Path(tempfile.gettempdir()).glob("verify-wheel-*")}
            lines = verifier.verify(Path(dist), "1.2.3")
            after = {p for p in Path(tempfile.gettempdir()).glob("verify-wheel-*")}
        self.assertEqual(len(lines), 7)
        self.assertTrue(all(line.startswith("ok ") for line in lines))
        self.assertEqual(after - before, set())

    def test_the_environment_line_names_the_interpreter_and_platform(self) -> None:
        with tempfile.TemporaryDirectory() as dist:
            write_wheel(Path(dist))
            lines = verifier.verify(Path(dist), "1.2.3")
        self.assertEqual(len(lines), 7)
        environment = lines[2]
        self.assertTrue(environment.startswith("ok environment:"))
        self.assertIn(platform.python_version(), environment)
        self.assertIn(platform.platform(), environment)


class MainTests(unittest.TestCase):
    def run_main(self, argv: list) -> tuple:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            status = verifier.main(argv)
        return status, out.getvalue(), err.getvalue()

    def test_no_wheel_exits_3_with_one_line_on_stderr(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            status, out, err = self.run_main(["--dist", tmp])
        self.assertEqual(status, 3)
        self.assertEqual(out, "")
        self.assertEqual(err.count("\n"), 1)
        self.assertIn("wheel check: no wheel found", err)

    def test_a_broken_wheel_fails_before_any_install(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            write_wheel(Path(tmp), entry_points=None)
            with mock.patch.object(verifier, "install") as install:
                status, _, err = self.run_main(["--dist", tmp])
        self.assertEqual(status, 3)
        self.assertIn("no entry_points.txt", err)
        install.assert_not_called()

    def test_dist_is_required(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as raised:
                verifier.main([])
        self.assertEqual(raised.exception.code, 2)


class SourceTextTests(unittest.TestCase):
    """The verifier is a packaging check; its source must stay out of the live evidence."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.source = (ROOT / "scripts" / "verify_wheel.py").read_text(encoding="utf-8")

    def test_it_never_touches_the_evidence_directory(self) -> None:
        self.assertNotIn("docs/evidence", self.source)

    def test_it_does_not_import_the_evidence_verifier(self) -> None:
        self.assertNotIn("verify_release_evidence", self.source)


@contextlib.contextmanager
def working_directory(path: Path):
    """Change the current directory for the block and restore it afterwards."""
    previous = os.getcwd()
    os.chdir(str(path))
    try:
        yield
    finally:
        os.chdir(previous)


def verify_wheel_dirs() -> set:
    return set(Path(tempfile.gettempdir()).glob("verify-wheel-*"))


class RelativeDistTests(unittest.TestCase):
    """``--dist`` may be relative or absolute; every path the checks use is absolute."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        # realpath: the temporary directory may sit behind a symlink (macOS /var)
        self.base = Path(os.path.realpath(self._tmp.name))

    def make_dist(self, name: str = "dist") -> Path:
        dist = self.base / name
        dist.mkdir()
        write_wheel(dist)
        return dist

    def run_main(self, argv: list) -> tuple:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            status = verifier.main(argv)
        return status, out.getvalue(), err.getvalue()

    def assert_seven_ok_lines(self, lines: list) -> None:
        self.assertEqual(len(lines), 7)
        self.assertTrue(all(line.startswith("ok ") for line in lines))

    def test_find_wheel_returns_an_absolute_path_for_a_relative_dist(self) -> None:
        dist = self.make_dist()
        absolute = verifier.find_wheel(dist)
        with working_directory(self.base):
            relative = verifier.find_wheel(Path("dist"))
        self.assertTrue(relative.is_absolute())
        self.assertEqual(relative, absolute)
        self.assertEqual(absolute, dist / absolute.name)

    def test_verify_passes_with_a_relative_dist_from_its_parent(self) -> None:
        self.make_dist()
        with working_directory(self.base):
            lines = verifier.verify(Path("dist"), "1.2.3")
        self.assert_seven_ok_lines(lines)

    def test_verify_passes_when_the_directory_name_has_spaces(self) -> None:
        self.make_dist("my dist dir")
        with working_directory(self.base):
            lines = verifier.verify(Path("my dist dir"), "1.2.3")
        self.assert_seven_ok_lines(lines)

    def test_verify_passes_with_a_relative_dist_from_another_directory(self) -> None:
        dist = self.make_dist()
        other = self.base / "other"
        other.mkdir()
        relative = os.path.relpath(str(dist), str(other))
        self.assertTrue(relative.startswith(".."))
        with working_directory(other):
            lines = verifier.verify(Path(relative), "1.2.3")
        self.assert_seven_ok_lines(lines)

    def test_main_returns_0_with_a_relative_dist(self) -> None:
        self.make_dist()
        with working_directory(self.base):
            status, out, err = self.run_main(["--dist", "dist", "--expect-version", "1.2.3"])
        self.assertEqual(status, 0, err)
        self.assertEqual(err, "")
        self.assertEqual(out.count("ok "), 7)

    def test_a_missing_wheel_and_a_missing_directory_name_the_directory_as_given(self) -> None:
        (self.base / "empty").mkdir()
        with working_directory(self.base):
            for given in ("empty", "missing"):
                with self.subTest(dist=given):
                    status, out, err = self.run_main(["--dist", given])
                    self.assertEqual(status, 3)
                    self.assertEqual(out, "")
                    self.assertEqual(err.count("\n"), 1)
                    self.assertIn(f"no wheel found in {given}", err)

    def test_two_wheels_exit_3_naming_both_without_installing(self) -> None:
        dist = self.base / "dist"
        dist.mkdir()
        write_wheel(dist, version="1.2.3")
        write_wheel(dist, version="1.2.4")
        with working_directory(self.base):
            with mock.patch.object(verifier, "install") as install:
                status, _, err = self.run_main(["--dist", "dist"])
        self.assertEqual(status, 3)
        self.assertEqual(err.count("\n"), 1)
        self.assertIn("1.2.3", err)
        self.assertIn("1.2.4", err)
        install.assert_not_called()

    def test_a_failed_install_names_the_absolute_wheel_and_leaves_no_directory(self) -> None:
        dist = self.make_dist()
        wheel = verifier.find_wheel(dist)
        failed = subprocess_result(1, stderr="no matching distribution")
        before = verify_wheel_dirs()
        with working_directory(self.base):
            with mock.patch.object(verifier, "run", return_value=failed):
                status, out, err = self.run_main(["--dist", "dist"])
        self.assertEqual(status, 3)
        self.assertEqual(err.count("\n"), 1)
        self.assertIn(str(wheel), err)
        self.assertIn("no matching distribution", err)
        self.assertEqual(verify_wheel_dirs() - before, set())

    def test_a_failed_later_check_leaves_no_directory(self) -> None:
        dist = self.make_dist()
        before = verify_wheel_dirs()
        boom = verifier.CheckError("runtime check exploded")
        with mock.patch.object(verifier, "check_runtime_absent", side_effect=boom):
            with self.assertRaisesRegex(verifier.CheckError, "runtime check exploded"):
                verifier.verify(dist, "1.2.3")
        self.assertEqual(verify_wheel_dirs() - before, set())

    def test_pip_gets_no_index_no_deps_and_the_absolute_wheel(self) -> None:
        dist = self.make_dist()
        expected = dist / "docker_for_apple_container_fork-1.2.3-py3-none-any.whl"
        with working_directory(self.base):
            wheel = verifier.find_wheel(Path("dist"))
        calls = []

        def fake_run(argv, *, cwd, env):
            calls.append(list(argv))
            return subprocess_result(0)

        env_dir = self.base / "venv"
        with mock.patch.object(verifier, "run", side_effect=fake_run):
            verifier.install(wheel, env_dir, self.base)
        self.assertEqual(len(calls), 1)
        argv = calls[0]
        self.assertIn("--no-index", argv)
        self.assertIn("--no-deps", argv)
        self.assertEqual(argv[-1], str(expected))
        self.assertTrue(os.path.isabs(argv[-1]))


def subprocess_result(returncode: int, stdout: str = "", stderr: str = ""):
    return subprocess.CompletedProcess([], returncode, stdout=stdout, stderr=stderr)


if __name__ == "__main__":
    unittest.main()
