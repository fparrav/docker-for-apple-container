from __future__ import annotations

import unittest

import compat_matrix

TIER = "unit"


def entry(**overrides):
    fields = {
        "id": "run",
        "kind": "command",
        "status": "SUPPORTED",
        "apple": "run",
        "evidence": "unit",
        "test": None,
        "note": "",
    }
    fields.update(overrides)
    return fields


class ValidateTests(unittest.TestCase):
    def test_valid_entry_has_no_errors(self):
        self.assertEqual(compat_matrix.validate([entry()]), [])

    def test_empty_matrix_is_valid(self):
        self.assertEqual(compat_matrix.validate([]), [])

    def test_non_list_is_reported(self):
        self.assertEqual(
            compat_matrix.validate({"id": "run"}),
            ["entries is not a list"],
        )

    def test_non_mapping_entry_is_reported(self):
        self.assertEqual(
            compat_matrix.validate(["nope"]),
            ["entries[0] is not a mapping"],
        )

    def test_missing_fields_are_reported(self):
        fields = entry()
        del fields["evidence"]
        del fields["test"]
        self.assertEqual(
            compat_matrix.validate([fields]),
            [
                "entries[0] (run): missing field 'evidence'",
                "entries[0] (run): missing field 'test'",
            ],
        )

    def test_unknown_field_is_reported(self):
        self.assertEqual(
            compat_matrix.validate([entry(extra="x")]),
            ["entries[0] (run): unknown field 'extra'"],
        )

    def test_bad_kind_is_reported(self):
        self.assertEqual(
            compat_matrix.validate([entry(kind="docker")]),
            [
                "entries[0] (run): kind 'docker' is not one of "
                "command, option, compose-key"
            ],
        )

    def test_bad_status_is_reported(self):
        self.assertEqual(
            compat_matrix.validate([entry(status="NOPE")]),
            [
                "entries[0] (run): status 'NOPE' is not one of "
                "SUPPORTED, PARTIAL, UNSUPPORTED"
            ],
        )

    def test_bad_evidence_is_reported(self):
        self.assertEqual(
            compat_matrix.validate([entry(evidence="magic")]),
            [
                "entries[0] (run): evidence 'magic' is not one of "
                "unit, fake, live, conformance"
            ],
        )

    def test_duplicate_ids_are_reported(self):
        self.assertEqual(
            compat_matrix.validate([entry(), entry(id="run")]),
            ["entries[1] (run): duplicate id 'run'"],
        )

    def test_distinct_ids_are_fine(self):
        self.assertEqual(
            compat_matrix.validate([entry(), entry(id="ps")]),
            [],
        )

    def test_live_entry_requires_a_test(self):
        self.assertEqual(
            compat_matrix.validate([entry(evidence="live")]),
            ["entries[0] (run): evidence 'live' requires a test id"],
        )

    def test_conformance_entry_requires_a_test(self):
        self.assertEqual(
            compat_matrix.validate([entry(evidence="conformance")]),
            ["entries[0] (run): evidence 'conformance' requires a test id"],
        )

    def test_empty_test_id_counts_as_missing(self):
        for blank in ("", None):
            with self.subTest(test=blank):
                self.assertEqual(
                    compat_matrix.validate([entry(evidence="live", test=blank)]),
                    ["entries[0] (run): evidence 'live' requires a test id"],
                )

    def test_live_entry_with_test_is_fine(self):
        self.assertEqual(
            compat_matrix.validate(
                [
                    entry(
                        evidence="live",
                        test="test_live_security.SecurityLiveTests.test_flag",
                    )
                ]
            ),
            [],
        )

    def test_unit_entry_with_test_is_fine(self):
        self.assertEqual(
            compat_matrix.validate([entry(test="test_cli.RunTests.test_argv")]),
            [],
        )

    def test_null_apple_test_and_note_are_allowed(self):
        self.assertEqual(
            compat_matrix.validate([entry(apple=None, note=None)]),
            [],
        )

    def test_entry_without_id_is_labelled_by_index(self):
        fields = entry()
        del fields["id"]
        self.assertEqual(
            compat_matrix.validate([fields]),
            ["entries[0] (#0): missing field 'id'"],
        )


class RenderTableTests(unittest.TestCase):
    def test_empty_matrix_renders_header_and_separator(self):
        self.assertEqual(
            compat_matrix.render_table([]).splitlines(),
            [
                "| id | status | apple | evidence | note |",
                "| --- | --- | --- | --- | --- |",
            ],
        )

    def test_rows_are_sorted_by_kind_then_id(self):
        entries = [
            entry(id="zeta", kind="option"),
            entry(id="alpha", kind="command", apple=None, note=None),
            entry(id="beta", kind="command"),
        ]
        self.assertEqual(
            compat_matrix.render_table(entries).splitlines(),
            [
                "| id | status | apple | evidence | note |",
                "| --- | --- | --- | --- | --- |",
                "| alpha | SUPPORTED | - | unit | - |",
                "| beta | SUPPORTED | run | unit | - |",
                "| zeta | SUPPORTED | run | unit | - |",
            ],
        )

    def test_pipe_in_note_is_escaped(self):
        table = compat_matrix.render_table([entry(note="unit | live")])
        self.assertEqual(
            table.splitlines()[-1],
            "| run | SUPPORTED | run | unit | unit \\| live |",
        )

    def test_empty_apple_and_note_render_dash(self):
        table = compat_matrix.render_table([entry(apple=None, note=None)])
        self.assertEqual(
            table.splitlines()[-1],
            "| run | SUPPORTED | - | unit | - |",
        )

    def test_missing_optional_fields_render_dash(self):
        fields = entry()
        del fields["apple"]
        del fields["note"]
        self.assertEqual(
            compat_matrix.render_table([fields]).splitlines()[-1],
            "| run | SUPPORTED | - | unit | - |",
        )


class LoadTests(unittest.TestCase):
    def test_matrix_path_points_at_the_repo_matrix(self):
        self.assertEqual(compat_matrix.MATRIX_PATH.name, "matrix.json")
        self.assertEqual(
            compat_matrix.MATRIX_PATH.parent.name,
            "compat",
        )
        self.assertTrue(compat_matrix.MATRIX_PATH.is_file())

    def test_load_reads_the_matrix(self):
        self.assertIsInstance(compat_matrix.load(), list)

    def test_loaded_matrix_is_valid(self):
        self.assertEqual(compat_matrix.validate(compat_matrix.load()), [])


if __name__ == "__main__":
    unittest.main()
