from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from container_docker_shim import mounts

TIER = "unit"


class MountParsingTests(unittest.TestCase):
    def test_source_classification(self):
        for source in ("./data", "../data", "/var/data", "~/data", "data/sub"):
            with self.subTest(source=source):
                self.assertTrue(mounts.is_host_path(source))
        for source in ("data", "cache-volume"):
            with self.subTest(source=source):
                self.assertFalse(mounts.is_host_path(source))

    def test_short_volume_named_volume_bind_and_modes(self):
        cases = [
            ("cache:/cache", ["--mount", "type=volume,source=cache,target=/cache"]),
            ("cache:/cache:ro", ["--mount", "type=volume,source=cache,target=/cache,readonly"]),
            ("cache:/cache:rw", ["--mount", "type=volume,source=cache,target=/cache"]),
            ("/host:/data:ro", ["--mount", "type=bind,source=/host,target=/data,readonly"]),
            ("./data:/data", ["--mount", f"type=bind,source={os.path.normpath('/project/data')},target=/data"]),
        ]
        for value, expected in cases:
            with self.subTest(value=value):
                self.assertEqual(mounts.parse_volume(value, cwd="/project"), expected)

    def test_home_path_expands(self):
        actual = mounts.parse_volume("~/data:/data", cwd="/project")
        self.assertEqual(actual, ["--mount", f"type=bind,source={os.path.expanduser('~/data')},target=/data"])

    def test_unknown_short_mode_is_rejected(self):
        with self.assertRaisesRegex(mounts.MountSpecError, "nocopy"):
            mounts.parse_volume("cache:/cache:nocopy")

    def test_selinux_short_modes_are_refused(self):
        for value in (
            "cache:/cache:z",
            "cache:/cache:Z",
            "/host:/data:z",
            "./data:/data:Z",
            "cache:/cache:ro,z",
            "/host:/data:ro,Z",
        ):
            with self.subTest(value=value):
                with self.assertRaisesRegex(mounts.MountSpecError, "SELinux"):
                    mounts.parse_volume_spec(value, cwd="/project")

    def test_supported_short_modes_remain_noops(self):
        self.assertEqual(
            mounts.parse_volume("cache:/cache:ro,rw,cached,delegated", cwd="/project"),
            ["--mount", "type=volume,source=cache,target=/cache,readonly"],
        )
        self.assertEqual(
            mounts.parse_volume("/host:/data:rw,cached,delegated", cwd="/project"),
            ["--mount", "type=bind,source=/host,target=/data"],
        )

    def test_long_mount_aliases_and_readonly(self):
        self.assertEqual(
            mounts.parse_mount("type=bind,src=./data,dst=/data,ro", cwd="/project"),
            ["--mount", "type=bind,source=/project/data,target=/data,readonly"],
        )
        self.assertEqual(
            mounts.parse_mount("source=cache,target=/cache,readonly"),
            ["--mount", "type=volume,source=cache,target=/cache,readonly"],
        )

    def test_tmpfs_mount_emits_tmpfs_option(self):
        self.assertEqual(
            mounts.parse_mount("type=tmpfs,target=/scratch"),
            ["--tmpfs", "/scratch"],
        )
        self.assertEqual(mounts.emit_tmpfs("/tmp", "rw,size=64m"), ["--tmpfs", "/tmp:rw,size=64m"])

    def test_unknown_type_key_and_invalid_targets_are_rejected(self):
        for value, message in (
            ("type=npipe,source=x,target=/x", "type"),
            ("type=bind,source=/x,target=/x,bind-propagation=rshared", "key"),
            ("type=bind,source=/x,target=relative", "absolute"),
        ):
            with self.subTest(value=value):
                with self.assertRaisesRegex(mounts.MountSpecError, message):
                    mounts.parse_mount(value)


if __name__ == "__main__":
    unittest.main()
