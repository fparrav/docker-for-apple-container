from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

import test_cli

TIER = "fake"

ROOT = Path(__file__).resolve().parents[1]
DOCKER = ROOT / "bin" / "docker"

# The shared fake in test_cli always exits 0 from `run`/`exec` and only ever
# writes to stdout, so it cannot prove the exit-code and stream contracts that
# automated callers rely on. This dedicated fake executes the requested
# command on the host instead: real exit codes, real stdout/stderr separation,
# and real stdin forwarding. It is stateless, which also lets it run under a
# minimal environment.
AGENT_FAKE_CONTAINER = r"""#!/usr/bin/env python3
import subprocess
import sys


def take(args, i):
    if "=" in args[i] and args[i].startswith("--"):
        return args[i].split("=", 1)[1], i + 1
    return args[i + 1], i + 2


args = sys.argv[1:]

if args == ["--version"]:
    print("container CLI version 1.5.0 (agent-fake)")
    raise SystemExit(0)

if args[:2] == ["system", "status"]:
    print("apiserver is running")
    raise SystemExit(0)

if args and args[0] == "run":
    value_opts = {
        "--name", "--label", "-w", "--workdir", "--cwd", "-e", "--env",
        "--mount", "--tmpfs", "--memory", "-m", "--cpus", "--user", "-u",
        "--cap-add", "--cap-drop", "--platform", "--publish", "-p",
        "--entrypoint", "--hostname", "--network",
    }
    bool_opts = {
        "-d", "--detach", "-i", "--interactive", "-t", "--tty",
        "--init", "--rm", "--read-only", "--no-dns",
    }
    detach = False
    name = None
    i = 1
    while i < len(args):
        key = args[i].split("=", 1)[0] if args[i].startswith("--") else args[i]
        if key in bool_opts:
            detach = key in ("-d", "--detach")
            i += 1
            continue
        if key in value_opts:
            value, i = take(args, i)
            if key == "--name":
                name = value
            continue
        break
    if detach or i >= len(args):
        print(name or "agent-fake")
        raise SystemExit(0)
    raise SystemExit(subprocess.run(args[i + 1:]).returncode)

if args and args[0] == "exec":
    i = 1
    while i < len(args) and args[i].startswith("-"):
        i += 2 if args[i] in ("-e", "-w", "--user", "--env-file") else 1
    ident = args[i] if i < len(args) else None
    command = args[i + 1:]
    if not ident or not command:
        print("No such container", file=sys.stderr)
        raise SystemExit(1)
    raise SystemExit(subprocess.run(command).returncode)

print("unsupported agent-fake args: " + " ".join(args), file=sys.stderr)
raise SystemExit(2)
"""


class AgentContractTests(test_cli.ShimCLITestCase):
    """What an automated agent can rely on from the shim CLI.

    Covers the contract from the change: PATH-only resolution in a minimal
    environment, exit-code propagation, and stdout/stderr separation for
    `exec` and `run`. The base fake cannot fail or write to stderr, so setUp
    swaps in the dedicated fake that executes commands on the host. Also pins
    the Engine-only capability claims that `info` must not make, and the
    explicit refusal of unmapped commands such as `commit`.
    """

    def setUp(self) -> None:
        super().setUp()
        self.fake.write_text(AGENT_FAKE_CONTAINER)
        self.fake.chmod(0o755)

    def _create_container(self, name: str) -> None:
        result = self.docker(
            "run", "-d", "--name", name, "alpine", "sleep", "infinity"
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_version_in_minimal_environment(self) -> None:
        # PATH-only resolution: no CONTAINER_DOCKER_SHIM_CONTAINER, no
        # shell config, nothing beyond PATH and HOME.
        bindir = self.root / "bin"
        bindir.mkdir()
        container = bindir / "container"
        container.write_text(AGENT_FAKE_CONTAINER)
        container.chmod(0o755)
        # Both the shim and the fake use a `#!/usr/bin/env python3` shebang,
        # so the minimal environment needs python3 reachable on PATH.
        (bindir / "python3").symlink_to(sys.executable)
        home = self.root / "home"
        home.mkdir()

        result = subprocess.run(
            [str(DOCKER), "version"],
            env={"PATH": str(bindir), "HOME": str(home)},
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(result.stdout.strip())

    def test_exec_exit_code_is_propagated(self) -> None:
        self._create_container("c")
        result = self.docker("exec", "c", "sh", "-c", "exit 7")
        self.assertEqual(result.returncode, 7)

    def test_exec_interactive_stdin_reaches_the_container(self) -> None:
        self._create_container("c")
        result = self.docker("exec", "-i", "c", "cat", input_text="agent-stdin\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "agent-stdin\n")

    def test_exec_stderr_stays_out_of_stdout(self) -> None:
        self._create_container("c")
        result = self.docker("exec", "c", "sh", "-c", "echo agent-stderr 1>&2")
        self.assertEqual(result.returncode, 0)
        self.assertIn("agent-stderr", result.stderr)
        self.assertNotIn("agent-stderr", result.stdout)

    def test_run_stderr_stays_out_of_stdout(self) -> None:
        result = self.docker("run", "alpine", "sh", "-c", "echo agent-stderr 1>&2")
        self.assertEqual(result.returncode, 0)
        self.assertIn("agent-stderr", result.stderr)
        self.assertNotIn("agent-stderr", result.stdout)

    def test_run_returns_container_exit_code(self) -> None:
        result = self.docker("run", "alpine", "sh", "-c", "exit 7")
        self.assertEqual(result.returncode, 7)

    def test_info_makes_no_engine_only_capability_claims(self) -> None:
        result = self.docker("info")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout,
            "Storage Driver: apple-container\nContainer Runtime: Apple container\n",
        )
        for claim in ("Swarm: active", "Swarm: pending", "Kubernetes"):
            self.assertNotIn(claim, result.stdout)

    def test_commit_is_refused_with_usage_error(self) -> None:
        result = self.docker("commit", "c", "img")
        self.assertEqual(result.returncode, 64)
        self.assertIn("commit", result.stderr)
        self.assertIn("unsupported Docker command", result.stderr)


if __name__ == "__main__":
    unittest.main()
