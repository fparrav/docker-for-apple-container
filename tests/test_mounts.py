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


class ShortModePolicyTests(unittest.TestCase):
    """check_short_modes reads MODE_RULES for bind and named sources."""

    def test_readonly_and_noop_verdicts(self):
        for source in ("/host/data", "cache"):
            for mode, expected in (
                ("ro", True),
                ("rw", False),
                ("cached", False),
                ("delegated", False),
                ("ro,rw,cached,delegated", True),
                ("rw,cached,delegated", False),
            ):
                with self.subTest(source=source, mode=mode):
                    self.assertEqual(mounts.check_short_modes(mode, "/data"), expected)

    def test_selinux_modes_are_refused_naming_option_and_target(self):
        for source in ("/host/data", "cache"):
            for mode, option in (
                ("z", "z"),
                ("Z", "Z"),
                ("ro,Z", "Z"),
                ("Z,ro", "Z"),
                ("cached,Z", "Z"),
            ):
                with self.subTest(source=source, mode=mode):
                    with self.assertRaises(mounts.MountSpecError) as caught:
                        mounts.check_short_modes(mode, "/data")
                    message = str(caught.exception)
                    self.assertIn(option, message)
                    self.assertIn("/data", message)
                    self.assertIn("SELinux", message)
                    self.assertIn("macOS", message)

    def test_unknown_mode_is_refused_naming_option_and_target(self):
        for source in ("/host/data", "cache"):
            with self.subTest(source=source):
                with self.assertRaises(mounts.MountSpecError) as caught:
                    mounts.check_short_modes("weird", "/data")
                message = str(caught.exception)
                self.assertIn("weird", message)
                self.assertIn("/data", message)

    def test_parse_volume_spec_applies_the_table(self):
        self.assertTrue(mounts.parse_volume_spec("cache:/cache:ro").readonly)
        self.assertTrue(mounts.parse_volume_spec("./data:/data:ro").readonly)
        self.assertFalse(mounts.parse_volume_spec("cache:/cache:rw").readonly)
        self.assertFalse(mounts.parse_volume_spec("/host:/data:cached").readonly)
        self.assertFalse(mounts.parse_volume_spec("/host:/data:delegated").readonly)
        for value, option, target in (
            ("cache:/cache:z", "z", "/cache"),
            ("cache:/cache:Z", "Z", "/cache"),
            ("/host:/data:ro,Z", "Z", "/data"),
            ("./data:/data:Z,ro", "Z", "/data"),
            ("cache:/cache:nocopy", "nocopy", "/cache"),
            ("/host:/data:weird", "weird", "/data"),
        ):
            with self.subTest(value=value):
                with self.assertRaises(mounts.MountSpecError) as caught:
                    mounts.parse_volume_spec(value, cwd="/project")
                message = str(caught.exception)
                self.assertIn(option, message)
                self.assertIn(target, message)


class LongSyntaxPolicyTests(unittest.TestCase):
    """mount_from_entry validates every key against ATTRIBUTE_RULES."""

    def _bind(self, **extra) -> dict:
        entry = {"type": "bind", "source": "./data", "target": "/data"}
        entry.update(extra)
        return entry

    def _named(self, **extra) -> dict:
        entry = {"type": "volume", "source": "cache", "target": "/cache"}
        entry.update(extra)
        return entry

    def _refused(self, entry: dict, option: str, target: str) -> str:
        with self.assertRaises(mounts.MountSpecError) as caught:
            mounts.mount_from_entry(entry, "/project")
        message = str(caught.exception)
        self.assertIn(option, message)
        self.assertIn(target, message)
        return message

    def test_source_less_volume_is_an_anonymous_mount(self):
        for entry in (
            {"type": "volume", "target": "/app/node_modules"},
            {"type": "volume", "target": "/app/node_modules", "volume": {}},
            {"target": "/app/node_modules"},
        ):
            with self.subTest(entry=entry):
                mount = mounts.mount_from_entry(entry, "/project")
                self.assertEqual(
                    (mount.kind, mount.source, mount.target, mount.readonly),
                    ("volume", "", "/app/node_modules", False),
                )
        read_only = mounts.mount_from_entry(
            {"type": "volume", "target": "/x", "read_only": True}, "/project"
        )
        self.assertTrue(read_only.readonly)

    def test_source_less_volume_keeps_refusing_unsupported_attributes(self):
        self._refused(
            {"type": "volume", "target": "/x", "volume": {"subpath": "a"}}, "volume.subpath", "/x"
        )
        self._refused(
            {"type": "volume", "target": "/x", "volume": {"nocopy": True}}, "volume.nocopy", "/x"
        )

    def test_source_less_bind_and_docker_run_stay_refused(self):
        with self.assertRaises(mounts.MountSpecError) as caught:
            mounts.mount_from_entry({"type": "bind", "target": "/x"}, "/project")
        self.assertIn("mount source is required", str(caught.exception))
        # `docker run -v /path` keeps its current verdict.
        with self.assertRaises(mounts.MountSpecError) as caught:
            mounts.parse_volume_spec("/x")
        self.assertIn("unsupported volume syntax", str(caught.exception))

    def test_read_only_attribute(self):
        self.assertTrue(mounts.mount_from_entry(self._bind(read_only=True), "/project").readonly)
        self.assertTrue(mounts.mount_from_entry(self._named(read_only=True), "/project").readonly)
        self.assertFalse(mounts.mount_from_entry(self._named(read_only=False), "/project").readonly)
        # A non-boolean read_only is refused, not guessed.
        self._refused(self._named(read_only="true"), "read_only", "/cache")

    def test_consistency_is_accepted_without_effect(self):
        for entry in (self._bind(consistency="cached"), self._named(consistency="delegated")):
            with self.subTest(entry=entry):
                self.assertFalse(mounts.mount_from_entry(entry, "/project").readonly)

    def test_create_host_path_is_accepted_without_effect(self):
        mount = mounts.mount_from_entry(self._bind(bind={"create_host_path": True}), "/project")
        self.assertEqual((mount.kind, mount.readonly), ("bind", False))

    def test_selinux_attribute_is_refused_naming_option_and_target(self):
        for entry in (
            self._bind(bind={"selinux": "z"}),
            self._bind(bind={"selinux": "Z"}),
            # The normalizer emits bind.selinux even for a named volume.
            {
                "type": "volume",
                "source": "data",
                "target": "/d",
                "bind": {"selinux": "Z"},
                "volume": {},
            },
        ):
            with self.subTest(entry=entry):
                message = self._refused(entry, "selinux", entry["target"])
                self.assertIn("SELinux", message)
                self.assertIn("macOS", message)

    def test_empty_selinux_is_accepted_without_effect(self):
        for value in ("", None):
            for entry in (
                self._bind(bind={"selinux": value}),
                self._named(bind={"selinux": value}),
            ):
                with self.subTest(value=value, entry=entry):
                    self.assertFalse(mounts.mount_from_entry(entry, "/project").readonly)

    def test_unsupported_attributes_are_refused_naming_option_and_target(self):
        for entry, option, target in (
            (self._bind(bind={"propagation": "rshared"}), "propagation", "/data"),
            (self._named(bind={"propagation": "rslave"}), "propagation", "/cache"),
            (self._named(volume={"nocopy": True}), "nocopy", "/cache"),
            (self._named(volume={"subpath": "/sub"}), "subpath", "/cache"),
        ):
            with self.subTest(entry=entry):
                self._refused(entry, option, target)

    def test_unknown_keys_are_refused_naming_option_and_target(self):
        for entry, option, target in (
            (self._bind(bind={"foo": 1}), "foo", "/data"),
            (self._named(volume={"foo": 1}), "foo", "/cache"),
            (self._bind(foo="bar"), "foo", "/data"),
            (self._named(foo="bar"), "foo", "/cache"),
        ):
            with self.subTest(entry=entry):
                self._refused(entry, option, target)

    def test_refused_and_unknown_types_name_type_and_target(self):
        for entry, option, target in (
            ({"type": "image", "source": "alpine", "target": "/img"}, "image", "/img"),
            ({"type": "tmpfs", "target": "/scratch"}, "tmpfs", "/scratch"),
            ({"type": "npipe", "source": "x", "target": "/x"}, "npipe", "/x"),
        ):
            with self.subTest(entry=entry):
                self._refused(entry, option, target)

    def test_comma_in_source_is_refused_naming_source_and_target(self):
        for entry in (
            {"type": "bind", "source": "/a,b", "target": "/data"},
            {"type": "volume", "source": "a,b", "target": "/cache"},
        ):
            with self.subTest(entry=entry):
                self._refused(entry, entry["source"], entry["target"])
        # The short syntax refuses the same source.
        with self.assertRaises(mounts.MountSpecError) as caught:
            mounts.parse_volume_spec("a,b:/data", cwd="/project")
        message = str(caught.exception)
        self.assertIn("a,b", message)
        self.assertIn("/data", message)

    def test_relative_target_is_refused_naming_the_target(self):
        for entry in (
            {"type": "bind", "source": "./data", "target": "relative"},
            {"type": "volume", "source": "cache", "target": "relative"},
        ):
            with self.subTest(entry=entry):
                self._refused(entry, "relative", "relative")
        with self.assertRaises(mounts.MountSpecError) as caught:
            mounts.parse_volume_spec("cache:relative", cwd="/project")
        self.assertIn("relative", str(caught.exception))

    def test_sources_resolve_against_the_project_directory(self):
        mount = mounts.mount_from_entry(self._bind(), "/project")
        self.assertEqual(
            (mount.kind, mount.source, mount.target, mount.readonly),
            ("bind", "/project/data", "/data", False),
        )
        mount = mounts.mount_from_entry(
            {"type": "bind", "source": "/abs", "target": "/data"}, "/project"
        )
        self.assertEqual((mount.kind, mount.source), ("bind", "/abs"))
        mount = mounts.mount_from_entry(self._named(), "/project", volume_name="p_cache")
        self.assertEqual((mount.kind, mount.source, mount.target), ("volume", "p_cache", "/cache"))
        mount = mounts.mount_from_entry(self._named(), "/project")
        self.assertEqual((mount.kind, mount.source), ("volume", "cache"))

    def test_non_mapping_entry_is_refused(self):
        with self.assertRaises(mounts.MountSpecError):
            mounts.mount_from_entry("cache:/cache", "/project")


if __name__ == "__main__":
    unittest.main()
