"""Contract tests for the Hermes agent against the shim CLI.

Hermes drives its agent sandboxes through the Docker CLI. Each command it
relies on, the output shape it consumes, and the test that pins it:

  Command                                  Output shape Hermes relies on        Pinned by
  ---------------------------------------  ----------------------------------  -----------------------------------
  ps --format                              one line per container:              test_hermes_lifecycle_contract
                                           '{{.ID}}<TAB>{{.State}}' renders as
                                           <name><TAB><state>
  inspect --format (labels, environment)   '{{json .Config.Labels}}' renders    test_inspect_exposes_labels_and_environment
                                           as a JSON object; '{{json
                                           .Config.Env}}' as a JSON list, e.g.
                                           ["FOO=bar"]
  logs                                     container stdout passthrough;        test_logs_forwards_follow_and_tail
                                           -f/--tail translated to Apple flags
  exec                                     stdin forwarded to the container,    test_hermes_lifecycle_contract
                                           echoed back on stdout
  build                                    exit 0, "built"; Apple build args    test_build_translates_the_agent_image_build
                                           (--file, --tag, --platform, ...)
  compose                                  '-p <project> ps' renders TSV rows   test_compose_ps_lists_project_containers
                                           NAME/IMAGE/STATE/SERVICE
"""

from __future__ import annotations

import datetime
import json

import test_cli

TIER = "fake"


class HermesContractTests(test_cli.ShimCLITestCase):
    def test_hermes_lifecycle_contract(self) -> None:
        version = self.docker("version")
        self.assertEqual(version.returncode, 0, version.stderr)
        self.assertIn("container CLI version 1.5.0", version.stdout)

        image = self.docker(
            "image",
            "inspect",
            "nikolaik/python-nodejs:python3.11-nodejs20",
            "--format",
            "{{json .Config.Entrypoint}}",
        )
        self.assertEqual(image.returncode, 0, image.stderr)
        self.assertEqual(image.stdout.strip(), '["docker-entrypoint.sh"]')

        # The contract inherits --security-opt/--pids-limit, which Apple container never applies and the shim now refuses without explicit opt-in.
        self.env["CONTAINER_DOCKER_SHIM_ALLOW_UNENFORCED"] = "security-opt,pids-limit"

        run = self.docker(
            "run",
            "-d",
            "--init",
            "--name",
            "hermes-test",
            "--label",
            "hermes-agent=1",
            "--label",
            "hermes-task-id=default",
            "--label",
            "hermes-profile=default",
            "-w",
            "/root",
            "--cap-drop",
            "ALL",
            "--cap-add",
            "DAC_OVERRIDE",
            "--cap-add",
            "CHOWN",
            "--cap-add",
            "FOWNER",
            "--security-opt",
            "no-new-privileges",
            "--pids-limit",
            "256",
            "--tmpfs",
            "/tmp:rw,nosuid,size=512m",
            "--tmpfs",
            "/var/tmp:rw,noexec,nosuid,size=256m",
            "--tmpfs",
            "/run:rw,noexec,nosuid,size=64m",
            "--tmpfs",
            "/workspace:rw,exec,size=10g",
            "--memory",
            "512m",
            "--cpus",
            "1.0",
            "-v",
            "/host:/workspace:ro",
            "-e",
            "FOO=bar",
            "nikolaik/python-nodejs:python3.11-nodejs20",
            "sleep",
            "infinity",
        )
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(run.stdout.strip(), "hermes-test")

        ps_ids = self.docker(
            "ps", "-a", "--filter", "label=hermes-agent=1", "--format", "{{.ID}}"
        )
        self.assertEqual(ps_ids.returncode, 0, ps_ids.stderr)
        self.assertEqual(ps_ids.stdout.strip(), "hermes-test")

        ps_state = self.docker(
            "ps",
            "-a",
            "--filter",
            "label=hermes-task-id=default",
            "--filter",
            "label=hermes-profile=default",
            "--format",
            "{{.ID}}\t{{.State}}",
        )
        self.assertEqual(ps_state.returncode, 0, ps_state.stderr)
        self.assertEqual(ps_state.stdout.strip(), "hermes-test\trunning")

        before_stop = self.docker(
            "inspect", "--format", "{{.State.FinishedAt}}", "hermes-test"
        )
        self.assertEqual(before_stop.returncode, 0, before_stop.stderr)
        self.assertEqual(before_stop.stdout.strip(), "0001-01-01T00:00:00Z")

        exec_result = self.docker(
            "exec",
            "-i",
            "-e",
            "TEST=1",
            "hermes-test",
            "bash",
            "-c",
            "cat",
            input_text="hello\n",
        )
        self.assertEqual(exec_result.returncode, 0, exec_result.stderr)
        self.assertEqual(exec_result.stdout, "hello\n")

        stop = self.docker("stop", "-t", "10", "hermes-test")
        self.assertEqual(stop.returncode, 0, stop.stderr)

        after_stop = self.docker(
            "inspect", "--format", "{{.State.FinishedAt}}", "hermes-test"
        )
        self.assertEqual(after_stop.returncode, 0, after_stop.stderr)
        self.assertEqual(after_stop.stdout.strip(), "2026-01-01T00:00:05Z")
        self.assertFalse(after_stop.stdout.strip().startswith("0001-01-01"))

        exited = self.docker(
            "ps", "-a", "--filter", "status=exited", "--format", "{{.ID}}"
        )
        self.assertEqual(exited.returncode, 0, exited.stderr)
        self.assertEqual(exited.stdout.strip(), "hermes-test")

        start = self.docker("start", "hermes-test")
        self.assertEqual(start.returncode, 0, start.stderr)

        rm = self.docker("rm", "-f", "hermes-test")
        self.assertEqual(rm.returncode, 0, rm.stderr)

        gone = self.docker(
            "ps", "-a", "--filter", "label=hermes-agent=1", "--format", "{{.ID}}"
        )
        self.assertEqual(gone.returncode, 0, gone.stderr)
        self.assertEqual(gone.stdout.strip(), "")

    def test_hermes_run_is_refused_without_opt_in(self) -> None:
        run = self.docker(
            "run",
            "-d",
            "--name",
            "hermes-refused",
            "--security-opt",
            "no-new-privileges",
            "--pids-limit",
            "256",
            "nikolaik/python-nodejs:python3.11-nodejs20",
            "sleep",
            "infinity",
        )
        self.assertEqual(run.returncode, 64, run.stderr)
        self.assertIn("--security-opt", run.stderr)
        for call in self.container_calls():
            self.assertNotIn(call[0], ("run", "create"))

    def test_orphan_reaper_receives_a_real_finished_time(self) -> None:
        run = self.docker(
            "run",
            "-d",
            "--name",
            "reap-me",
            "--label",
            "hermes-agent=1",
            "alpine",
            "sleep",
            "infinity",
        )
        self.assertEqual(run.returncode, 0, run.stderr)
        stop = self.docker("stop", "reap-me")
        self.assertEqual(stop.returncode, 0, stop.stderr)

        out = self.docker("inspect", "--format", "{{.State.FinishedAt}}", "reap-me")
        self.assertEqual(out.returncode, 0, out.stderr)
        stamp = out.stdout.strip()
        self.assertFalse(stamp.startswith("0001-01-01"))
        datetime.datetime.fromisoformat(stamp.replace("Z", "+00:00"))

    def test_inspect_exposes_labels_and_environment(self) -> None:
        run = self.docker(
            "run",
            "-d",
            "--name",
            "hermes-inspect",
            "--label",
            "hermes-agent=1",
            "--label",
            "hermes-task-id=default",
            "-e",
            "HERMES_HOME=/workspace",
            "alpine",
            "sleep",
            "infinity",
        )
        self.assertEqual(run.returncode, 0, run.stderr)

        labels = self.docker(
            "inspect", "--format", "{{json .Config.Labels}}", "hermes-inspect"
        )
        self.assertEqual(labels.returncode, 0, labels.stderr)
        self.assertEqual(
            labels.stdout.strip(),
            '{"hermes-agent":"1","hermes-task-id":"default"}',
        )

        env = self.docker(
            "inspect", "--format", "{{json .Config.Env}}", "hermes-inspect"
        )
        self.assertEqual(env.returncode, 0, env.stderr)
        self.assertEqual(env.stdout.strip(), '["HERMES_HOME=/workspace"]')

    def test_logs_forwards_follow_and_tail(self) -> None:
        run = self.docker(
            "run", "-d", "--name", "hermes-logs", "alpine", "sleep", "infinity"
        )
        self.assertEqual(run.returncode, 0, run.stderr)

        tailed = self.docker("logs", "-f", "--tail", "100", "hermes-logs")
        self.assertEqual(tailed.returncode, 0, tailed.stderr)
        self.assertEqual(tailed.stdout.strip(), "LOGS --follow -n 100 hermes-logs")

        all_logs = self.docker("logs", "--tail", "all", "hermes-logs")
        self.assertEqual(all_logs.returncode, 0, all_logs.stderr)
        self.assertEqual(all_logs.stdout.strip(), "LOGS hermes-logs")

    def test_build_translates_the_agent_image_build(self) -> None:
        self.env["FAKE_CONTAINER_BUILD_ARGS"] = str(self.root / "hermes-build-args.json")

        result = self.docker(
            "build",
            "-f",
            "images/Dockerfile.hermes",
            "-t",
            "hermes/agent:latest",
            "--platform",
            "linux/arm64",
            "--memory",
            "2g",
            "images",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "built")
        args = json.loads((self.root / "hermes-build-args.json").read_text())
        self.assertEqual(
            args,
            [
                "build",
                "--file",
                "images/Dockerfile.hermes",
                "--tag",
                "hermes/agent:latest",
                "--platform",
                "linux/arm64",
                "--memory",
                "2G",
                "images",
            ],
        )

    def test_compose_ps_lists_project_containers(self) -> None:
        run = self.docker(
            "run",
            "-d",
            "--name",
            "hermes-gateway-web",
            "--label",
            "com.docker.compose.project=hermes-gateway",
            "--label",
            "com.docker.compose.service=web",
            "alpine",
            "sleep",
            "infinity",
        )
        self.assertEqual(run.returncode, 0, run.stderr)

        result = self.docker("compose", "-p", "hermes-gateway", "ps", "--format", "json")
        self.assertEqual(result.returncode, 0, result.stderr)
        record = json.loads(result.stdout)
        self.assertEqual(record["Name"], "hermes-gateway-web")
        self.assertEqual(record["Project"], "hermes-gateway")
        self.assertEqual(record["Service"], "web")
        self.assertEqual(record["State"], "running")
        self.assertIsInstance(record["ExitCode"], int)


if __name__ == "__main__":
    import unittest

    unittest.main()
