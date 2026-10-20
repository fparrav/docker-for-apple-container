"""Unit contracts for the pure Compose ps renderers."""

from __future__ import annotations

import contextlib
import io
import json
import pathlib
import shlex
import sys
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from container_docker_shim import compose
from container_docker_shim.compose_ps import JSON_FIELDS, render_json, render_table

TIER = "unit"
ROOT = pathlib.Path(__file__).parent


def row(name: str, state: str = "running", service: str = "web") -> dict:
    return {
        "id": "0123456789abcdef",
        "name": name,
        "image": "busybox:1.36",
        "state": state,
        "path": "/bin/sh",
        "args": ["-c", "sleep 600"],
        "created_at": "2026-10-19 20:10:30 -0300 -03",
        "labels": {
            "com.docker.compose.project": "demo",
            "com.docker.compose.service": service,
        },
        "mounts": [],
        "networks": ["demo_default"],
        "ports": [],
    }


class ComposePsRendererTests(unittest.TestCase):
    def test_json_has_reference_keys_and_types(self) -> None:
        reference_path = ROOT / "fixtures/compat/ps-reference/all.json"
        reference = json.loads(reference_path.read_text().splitlines()[0])
        actual = json.loads(render_json([row("demo-web-1")]))
        self.assertEqual(tuple(actual), JSON_FIELDS)
        self.assertEqual(set(actual), set(reference))
        self.assertEqual({key: type(value) for key, value in actual.items()},
                         {key: type(value) for key, value in reference.items()})

    def test_json_is_one_record_per_line_and_empty_is_empty(self) -> None:
        self.assertEqual(render_json([]), "")
        self.assertEqual(len(render_json([row("a"), row("b")]).splitlines()), 2)

    def test_json_preserves_created_running_and_exited_states(self) -> None:
        exited = row("done", "exited")
        exited["exit_code"] = 3
        records = [
            json.loads(line)
            for line in render_json([row("new", "created"), row("live"), exited]).splitlines()
        ]
        self.assertEqual([record["State"] for record in records], ["created", "running", "exited"])
        self.assertEqual(records[2]["ExitCode"], 3)
        self.assertTrue(records[2]["Status"].startswith("Exited (3) "), records[2]["Status"])

    def test_json_refuses_exited_container_without_exit_code(self) -> None:
        # The Apple runtime exposes no numeric exit code; the renderer must
        # refuse rather than fabricate the 0 Docker would never print here.
        with self.assertRaises(ValueError) as ctx:
            render_json([row("done", "exited")])
        self.assertIn("done", str(ctx.exception))

    def test_table_omits_unavailable_exit_code_instead_of_zeroing(self) -> None:
        output = render_table([row("done", "exited")])
        self.assertIn("Exited", output)
        self.assertNotIn("(0)", output)
        self.assertNotIn("(3)", output)

    def test_cmd_ps_refuses_json_when_exited_lacks_code(self) -> None:
        from container_docker_shim.cli import ShimError

        output = io.StringIO()
        with mock.patch.object(
            compose, "_project_containers", return_value=[row("demo-web-1", "exited")]
        ):
            with contextlib.redirect_stdout(output):
                with self.assertRaises(ShimError) as ctx:
                    compose.cmd_ps("demo", show_all=True, output_format="json")
        self.assertEqual(ctx.exception.code, 64)
        self.assertIn("exit code", str(ctx.exception))
        self.assertEqual(output.getvalue(), "")

    def test_cmd_ps_table_quiet_and_services_still_list_exited(self) -> None:
        exited = row("demo-web-1", "exited")
        output = io.StringIO()
        with mock.patch.object(compose, "_project_containers", return_value=[exited]):
            with contextlib.redirect_stdout(output):
                self.assertEqual(compose.cmd_ps("demo", show_all=True), 0)
            table = output.getvalue()
            self.assertIn("Exited", table)
            self.assertNotIn("(0)", table)
            output.truncate(0)
            output.seek(0)
            with contextlib.redirect_stdout(output):
                self.assertEqual(compose.cmd_ps("demo", show_all=True, quiet=True), 0)
            self.assertEqual(output.getvalue(), "0123456789abcdef\n")
            output.truncate(0)
            output.seek(0)
            with contextlib.redirect_stdout(output):
                self.assertEqual(compose.cmd_ps("demo", show_all=True, services_only=True), 0)
            self.assertEqual(output.getvalue(), "web\n")

    def test_cmd_ps_json_lists_exited_when_code_is_verified(self) -> None:
        exited = row("demo-web-1", "exited")
        exited["exit_code"] = 3
        output = io.StringIO()
        with mock.patch.object(compose, "_project_containers", return_value=[exited]):
            with contextlib.redirect_stdout(output):
                self.assertEqual(compose.cmd_ps("demo", show_all=True, output_format="json"), 0)
        record = json.loads(output.getvalue())
        self.assertEqual(record["ExitCode"], 3)
        self.assertEqual(record["State"], "exited")

    def test_table_header_and_empty_project_match_reference(self) -> None:
        fixture = ROOT / "fixtures/compat/ps-reference/empty.table"
        self.assertEqual(render_table([]), fixture.read_text())
        headers = render_table([row("demo-web-1")]).splitlines()[0].split()
        self.assertEqual(headers, ["NAME", "IMAGE", "COMMAND", "SERVICE", "CREATED", "STATUS", "PORTS"])

    def test_table_matches_captured_three_state_reference(self) -> None:
        fixture_dir = ROOT / "fixtures/compat/ps-reference"
        expected = (fixture_dir / "all.table").read_text()
        rows = []
        for line in (fixture_dir / "all.json").read_text().splitlines():
            record = json.loads(line)
            words = shlex.split(record["Command"][1:-1])
            labels = dict(item.split("=", 1) for item in record["Labels"].split(",") if "=" in item)
            rows.append({
                "id": record["ID"], "name": record["Name"], "image": record["Image"],
                "state": record["State"], "exit_code": record["ExitCode"],
                "path": words[0], "args": words[1:], "created_at": record["CreatedAt"],
                "running_for": record["RunningFor"], "labels": labels,
            })
        self.assertEqual(render_table(rows), expected)

    def test_runtime_failure_does_not_write_partial_stdout(self) -> None:
        from container_docker_shim.cli import ShimError

        output = io.StringIO()
        with mock.patch.object(compose, "_project_containers", side_effect=ShimError("list failed", 1)):
            with contextlib.redirect_stdout(output):
                with self.assertRaises(ShimError):
                    compose.cmd_ps("demo")
        self.assertEqual(output.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
