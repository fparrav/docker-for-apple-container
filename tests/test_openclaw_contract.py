"""Contract tests for OpenClaw against the shim CLI.

OpenClaw keeps a long-lived sandbox container and drives it through the Docker
CLI. Each command it relies on, the output shape it consumes, and the test
that pins it:

  Command                                   Output shape OpenClaw relies on    Pinned by
  ----------------------------------------  --------------------------------  -------------------------------
  sandbox probe (inspect -f                 "true" while running, "false"     test_sandbox_running_probe
  {{.State.Running}})                       while stopped
  inspect --format (labels, environment)    '{{json .Config.Labels}}' renders test_inspect_exposes_labels_and_environment
                                           as a JSON object; '{{json
                                           .Config.Env}}' as a JSON list, e.g.
                                           ["OPENCLAW_HOME=/workspace"]
  exec                                      stdin forwarded to the sandbox,   test_exec_forwards_stdin_and_exit_code
                                           echoed back on stdout
  logs                                      container stdout passthrough      test_logs_passes_through
"""

from __future__ import annotations

import test_cli

TIER = "fake"


class OpenClawContractTests(test_cli.ShimCLITestCase):
    def test_sandbox_running_probe(self) -> None:
        run = self.docker(
            "run", "-d", "--name", "openclaw-sandbox", "alpine", "sleep", "infinity"
        )
        self.assertEqual(run.returncode, 0, run.stderr)

        running = self.docker(
            "inspect", "-f", "{{.State.Running}}", "openclaw-sandbox"
        )
        self.assertEqual(running.returncode, 0, running.stderr)
        self.assertEqual(running.stdout.strip(), "true")

        stop = self.docker("stop", "openclaw-sandbox")
        self.assertEqual(stop.returncode, 0, stop.stderr)
        stopped = self.docker(
            "inspect", "-f", "{{.State.Running}}", "openclaw-sandbox"
        )
        self.assertEqual(stopped.returncode, 0, stopped.stderr)
        self.assertEqual(stopped.stdout.strip(), "false")

    def test_inspect_exposes_labels_and_environment(self) -> None:
        run = self.docker(
            "run",
            "-d",
            "--name",
            "openclaw-sandbox",
            "--label",
            "openclaw.sandbox=1",
            "-e",
            "OPENCLAW_HOME=/workspace",
            "alpine",
            "sleep",
            "infinity",
        )
        self.assertEqual(run.returncode, 0, run.stderr)

        labels = self.docker(
            "inspect", "--format", "{{json .Config.Labels}}", "openclaw-sandbox"
        )
        self.assertEqual(labels.returncode, 0, labels.stderr)
        self.assertEqual(labels.stdout.strip(), '{"openclaw.sandbox":"1"}')

        env = self.docker(
            "inspect", "--format", "{{json .Config.Env}}", "openclaw-sandbox"
        )
        self.assertEqual(env.returncode, 0, env.stderr)
        self.assertEqual(env.stdout.strip(), '["OPENCLAW_HOME=/workspace"]')

    def test_exec_forwards_stdin_and_exit_code(self) -> None:
        run = self.docker(
            "run", "-d", "--name", "openclaw-sandbox", "alpine", "sleep", "infinity"
        )
        self.assertEqual(run.returncode, 0, run.stderr)

        result = self.docker(
            "exec", "-i", "openclaw-sandbox", "cat", input_text="openclaw\n"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "openclaw\n")

    def test_logs_passes_through(self) -> None:
        run = self.docker(
            "run", "-d", "--name", "openclaw-sandbox", "alpine", "sleep", "infinity"
        )
        self.assertEqual(run.returncode, 0, run.stderr)

        result = self.docker("logs", "--tail", "all", "openclaw-sandbox")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "LOGS openclaw-sandbox")


if __name__ == "__main__":
    import unittest

    unittest.main()
