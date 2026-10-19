from __future__ import annotations

import json
import platform
import subprocess
import sys

import test_cli

TIER = "fake"


class ImagePresentationTests(test_cli.ShimCLITestCase):
    """Docker image output backed by Apple image JSON.

    Docker behavior references:
    https://docs.docker.com/reference/cli/docker/image/inspect/
    https://docs.docker.com/reference/cli/docker/image/ls/
    """

    def test_image_commands_reject_non_array_runtime_schemas(self) -> None:
        self.docker("create", "--name", "fixture", "alpine", "true")
        for payload in [{"images": []}, {"items": []}, ["invalid"]]:
            self.update_fake_state(image_payload=payload)
            for command in [("images",), ("image", "inspect", "alpine")]:
                with self.subTest(payload=payload, command=command):
                    result = self.docker(*command)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(result.stdout, "")
                    self.assertIn("expected", result.stderr)

    def _default_image_payload(self, image: str) -> list[dict]:
        result = subprocess.run(
            [sys.executable, str(self.fake), "image", "inspect", image],
            text=True,
            capture_output=True,
            env=self.env,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def _rewrite_variant_architecture(
        self, payload: list[dict], architecture: str
    ) -> list[dict]:
        for item in payload:
            for variant in item.get("variants") or []:
                for section in (variant.get("platform"), variant.get("config")):
                    if isinstance(section, dict):
                        section["architecture"] = architecture
        return payload

    def test_image_inspect_default_is_docker_shaped(self) -> None:
        result = self.docker("image", "inspect", "example/app:1.0")
        self.assertEqual(result.returncode, 0, result.stderr)
        [obj] = json.loads(result.stdout)

        self.assertEqual(obj["Id"], "sha256:" + "b" * 64)
        self.assertEqual(obj["RepoTags"], ["example/app:1.0"])
        self.assertEqual(
            obj["RepoDigests"], ["example/app@sha256:" + "b" * 64]
        )
        self.assertEqual(obj["Created"], "2026-01-02T03:04:05Z")
        self.assertEqual(obj["Size"], 12345678)
        self.assertEqual(obj["Architecture"], "arm64")
        self.assertEqual(obj["Os"], "linux")
        self.assertEqual(obj["Config"]["Entrypoint"], ["docker-entrypoint.sh"])
        self.assertEqual(obj["Config"]["Cmd"], ["serve"])
        self.assertEqual(obj["Config"]["WorkingDir"], "/app")
        self.assertEqual(obj["RootFS"]["Layers"], ["sha256:" + "f" * 64])

    def test_image_inspect_reports_arm64_architecture(self) -> None:
        self.docker("create", "--name", "fixture", "alpine", "true")
        payload = self._rewrite_variant_architecture(
            self._default_image_payload("example/app:1.0"), "arm64"
        )
        self.update_fake_state(image_payload=payload)

        result = self.docker("image", "inspect", "example/app:1.0")
        self.assertEqual(result.returncode, 0, result.stderr)
        [obj] = json.loads(result.stdout)
        self.assertEqual(obj["Architecture"], "arm64")
        self.assertEqual(obj["Os"], "linux")

        formatted = self.docker(
            "image", "inspect", "--format", "{{.Architecture}}", "example/app:1.0"
        )
        self.assertEqual(formatted.returncode, 0, formatted.stderr)
        self.assertEqual(formatted.stdout, "arm64\n")

    def test_image_inspect_reports_amd64_architecture(self) -> None:
        self.docker("create", "--name", "fixture", "alpine", "true")
        payload = self._rewrite_variant_architecture(
            self._default_image_payload("example/app:1.0"), "amd64"
        )
        self.update_fake_state(image_payload=payload)

        result = self.docker("image", "inspect", "example/app:1.0")
        self.assertEqual(result.returncode, 0, result.stderr)
        [obj] = json.loads(result.stdout)
        self.assertEqual(obj["Architecture"], "amd64")
        self.assertEqual(obj["Os"], "linux")

        formatted = self.docker(
            "image", "inspect", "--format", "{{.Architecture}}", "example/app:1.0"
        )
        self.assertEqual(formatted.returncode, 0, formatted.stderr)
        self.assertEqual(formatted.stdout, "amd64\n")

    def test_image_inspect_formats_multiple_images_in_order(self) -> None:
        result = self.docker(
            "image",
            "inspect",
            "-f",
            "{{.RepoTags}}",
            "example/app:1.0",
            "example/multi:latest",
        )
        self.assertEqual(result.returncode, 64)
        self.assertEqual(result.stdout, "")
        self.assertIn("use '{{json .RepoTags}}'", result.stderr)

        rendered = self.docker(
            "image",
            "inspect",
            "--format",
            "{{json .RepoTags}}",
            "example/app:1.0",
            "example/multi:latest",
        )
        self.assertEqual(rendered.returncode, 0, rendered.stderr)
        self.assertEqual(
            rendered.stdout.splitlines(),
            ['["example/app:1.0"]', '["example/multi:latest"]'],
        )

    def test_image_inspect_platform_and_format_aliases(self) -> None:
        commands = (
            ("-f", "{{json .Config.Entrypoint}}"),
            ("-f={{json .Config.Entrypoint}}",),
            ("--format={{json .Config.Entrypoint}}",),
        )
        for format_args in commands:
            with self.subTest(format_args=format_args):
                result = self.docker(
                    "image",
                    "inspect",
                    "--platform",
                    "linux/amd64",
                    *format_args,
                    "example/multi:latest",
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), '["/amd64-entrypoint"]')

        missing = self.docker(
            "image",
            "inspect",
            "--platform=linux/s390x",
            "example/multi:latest",
        )
        self.assertEqual(missing.returncode, 1)
        self.assertIn("no platform matching linux/s390x", missing.stderr)

    def test_image_inspect_multi_variant_index_reports_matching_variant(self) -> None:
        # Without --platform the shim selects the variant matching the host
        # platform and falls back to the first variant when nothing matches.
        machine = platform.machine().lower()
        host_arch = {"x86_64": "amd64", "aarch64": "arm64"}.get(machine, machine)
        entrypoints = {
            "arm64": ["/arm64-entrypoint"],
            "amd64": ["/amd64-entrypoint"],
        }
        expected_arch = host_arch if host_arch in entrypoints else "arm64"
        expected_entrypoint = entrypoints.get(host_arch, entrypoints["arm64"])

        result = self.docker("image", "inspect", "example/multi:latest")
        self.assertEqual(result.returncode, 0, result.stderr)
        [obj] = json.loads(result.stdout)
        self.assertEqual(obj["Architecture"], expected_arch)
        self.assertEqual(obj["Os"], "linux")
        self.assertEqual(obj["Config"]["Entrypoint"], expected_entrypoint)

    def test_image_inspect_json_format_prints_one_object_per_image(self) -> None:
        result = self.docker(
            "image",
            "inspect",
            "--format=json",
            "example/app:1.0",
            "example/multi:latest",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        objects = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual(
            [obj["RepoTags"][0] for obj in objects],
            ["example/app:1.0", "example/multi:latest"],
        )

    def test_image_list_default_quiet_format_and_digests(self) -> None:
        default = self.docker("images")
        self.assertEqual(default.returncode, 0, default.stderr)
        lines = default.stdout.splitlines()
        self.assertEqual(lines[0], "REPOSITORY\tTAG\tIMAGE ID\tCREATED\tSIZE")
        self.assertIn("example/app\t1.0\tbbbbbbbbbbbb", lines[1])

        quiet = self.docker("image", "ls", "--quiet")
        self.assertEqual(quiet.returncode, 0, quiet.stderr)
        self.assertEqual(quiet.stdout.splitlines(), ["bbbbbbbbbbbb", "cccccccccccc"])

        formatted = self.docker(
            "images", "--format", "{{.Repository}}:{{.Tag}} {{.Size}}"
        )
        self.assertEqual(formatted.returncode, 0, formatted.stderr)
        self.assertEqual(
            formatted.stdout.splitlines(),
            ["example/app:1.0 12.3MB", "example/multi:latest 12.3MB"],
        )

        digests = self.docker("images", "--digests", "--no-trunc")
        self.assertEqual(digests.returncode, 0, digests.stderr)
        self.assertEqual(
            digests.stdout.splitlines()[0],
            "REPOSITORY\tTAG\tDIGEST\tIMAGE ID\tCREATED\tSIZE",
        )
        self.assertIn("sha256:" + "b" * 64, digests.stdout)

    def test_image_list_json_and_unsupported_fields(self) -> None:
        result = self.docker("images", "--format=json")
        self.assertEqual(result.returncode, 0, result.stderr)
        rows = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual(rows[0]["Repository"], "example/app")
        self.assertEqual(rows[0]["Tag"], "1.0")

        unsupported = self.docker("images", "--format", "{{.Containers}}")
        self.assertEqual(unsupported.returncode, 64)
        self.assertEqual(unsupported.stdout, "")
        self.assertIn("unsupported image list field: .Containers", unsupported.stderr)

    def test_image_list_uses_variant_size_and_hides_epoch_creation(self) -> None:
        self.docker("create", "--name", "fixture", "alpine", "true")
        self.update_fake_state(
            image_payload=[
                {
                    "id": "sha256:" + "d" * 64,
                    "configuration": {
                        "name": "example/big:1",
                        "creationDate": "2026-09-19T00:38:59Z",
                        "descriptor": {
                            "digest": "sha256:" + "d" * 64,
                            "mediaType":
                            "application/vnd.oci.image.index.v1+json",
                            "size": 10237,
                        },
                    },
                    "variants": [
                        {
                            "digest": "sha256:" + "d" * 64,
                            "size": 159971863,
                            "platform": {
                                "architecture": "arm64",
                                "os": "linux",
                                "variant": "v8",
                            },
                            "config": {
                                "created": "2026-09-19T00:38:59.786253347Z"
                            },
                        }
                    ],
                },
                {
                    "id": "sha256:" + "e" * 64,
                    "configuration": {
                        "name": "example/epoch:1",
                        "creationDate": "1970-01-01T00:00:00Z",
                        "descriptor": {
                            "digest": "sha256:" + "e" * 64,
                            "mediaType":
                            "application/vnd.oci.image.index.v1+json",
                            "size": 306,
                        },
                    },
                    "variants": [
                        {
                            "digest": "sha256:" + "e" * 64,
                            "size": 69078349,
                            "platform": {
                                "architecture": "arm64",
                                "os": "linux",
                                "variant": "v8",
                            },
                            "config": {},
                        }
                    ],
                },
            ]
        )
        result = self.docker(
            "images", "--format", "{{.Repository}} {{.Size}} {{.CreatedSince}}"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = result.stdout.splitlines()
        self.assertEqual(len(lines), 2)
        self.assertTrue(lines[0].startswith("example/big 160MB "))
        self.assertNotIn("years ago", lines[1])
        self.assertEqual(lines[1], "example/epoch 69.1MB N/A")


if __name__ == "__main__":
    import unittest

    unittest.main()
