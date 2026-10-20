"""Tests for ``scripts/test_report.py``.

These exercise the report script's pure logic with synthetic result sets and
never launch a subprocess: the five-outcome classification (design D4), the
tier filter, the D5 matrix join, the exit-status rule, and the release-notes
rendering. Synthetic test modules are built in memory and driven through the
real ``run_suite`` so the tier filter and skip reclassification are tested on
the real code path, while the synthetic test bodies only pass, skip or fail in
process.
"""

from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _p in (ROOT / "scripts", ROOT / "tests"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import _outcomes  # noqa: E402
import test_report  # noqa: E402

TIER = "unit"


# -- synthetic module helpers ------------------------------------------------


def _make_case(cls_name: str, module_name: str, method_name: str, behavior: str, reason: str):
    """Build a ``TestCase`` subclass whose single method passes, skips or fails.

    The class's ``__module__`` is set to ``module_name`` so the report script
    can read the declaring module's ``TIER``.
    """

    def method(self) -> None:
        if behavior == "pass":
            self.assertTrue(True)
        elif behavior == "skip":
            self.skipTest(reason)
        else:  # "fail"
            self.fail(reason)

    return type(cls_name, (unittest.TestCase,), {"__module__": module_name, method_name: method})


def _make_module(name: str, tier: str, specs) -> types.ModuleType:
    """Create and register a synthetic test module with the given tier.

    ``specs`` is a list of ``(method_name, behavior, reason)`` tuples. The
    module is registered in ``sys.modules`` under ``name`` so the report
    script's tier lookup (``sys.modules[module].TIER``) resolves.
    """
    module = types.ModuleType(name)
    module.TIER = tier
    for index, (method_name, behavior, reason) in enumerate(specs):
        cls = _make_case("Case%d" % index, name, method_name, behavior, reason)
        setattr(module, cls.__name__, cls)
    sys.modules[name] = module
    return module


def _run(module, enabled) -> list:
    """Run ``module``'s tests through the report script with ``enabled`` tiers."""
    return test_report.run_suite({module.__name__: module}, enabled)


# -- D4: five outcomes -------------------------------------------------------


class TestOutcomeClassification(unittest.TestCase):
    def test_disabled_tier_is_not_run(self):
        """A tier that is not enabled reports all of its tests as not run."""
        module = _make_module("syn_live_disabled", "live", [("test_a", "pass", None)])
        records = _run(module, {"unit", "fake"})
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["tier"], "live")
        self.assertEqual(records[0]["outcome"], test_report.NOT_RUN)
        self.assertEqual(records[0]["reason"], test_report.NOT_RUN_REASON)

    def test_enabled_tier_precondition_skip_is_not_run(self):
        """An enabled tier whose gate precondition fails is not run, not skipped."""
        reason = _outcomes.LIVE_TIER_CANNOT_RUN_PREFIX + " container runtime not available"
        module = _make_module("syn_live_gate", "live", [("test_a", "skip", reason)])
        records = _run(module, {"unit", "fake", "live"})
        self.assertEqual(records[0]["outcome"], test_report.NOT_RUN)
        self.assertEqual(records[0]["reason"], reason)

    def test_enabled_conformance_docker_not_set_is_not_run(self):
        reason = _outcomes.CONFORMANCE_DOCKER_NOT_SET
        module = _make_module("syn_conf_gate", "conformance", [("test_a", "skip", reason)])
        records = _run(module, {"unit", "fake", "conformance"})
        self.assertEqual(records[0]["outcome"], test_report.NOT_RUN)
        self.assertEqual(records[0]["reason"], reason)

    def test_ordinary_skip_stays_skipped(self):
        """A skip that is not a tier-gate precondition remains skipped."""
        reason = "an ordinary, test-level reason"
        module = _make_module("syn_unit_skip", "unit", [("test_a", "skip", reason)])
        records = _run(module, {"unit", "fake"})
        self.assertEqual(records[0]["outcome"], test_report.SKIPPED)
        self.assertEqual(records[0]["reason"], reason)

    def test_missing_image_skip_stays_skipped(self):
        """A per-test missing-image precondition is a skip, not a tier gate."""
        reason = (
            _outcomes.LIVE_IMAGES_NOT_LOCAL_PREFIX
            + "nginx:latest"
            + _outcomes.LIVE_IMAGES_NOT_LOCAL_SUFFIX
        )
        module = _make_module("syn_live_img", "live", [("test_a", "skip", reason)])
        records = _run(module, {"unit", "fake", "live"})
        self.assertEqual(records[0]["outcome"], test_report.SKIPPED)
        self.assertEqual(records[0]["reason"], reason)

    def test_pass_and_fail_outcomes(self):
        module = _make_module(
            "syn_unit_pf", "unit", [("test_ok", "pass", None), ("test_bad", "fail", "boom")]
        )
        records = _run(module, {"unit", "fake"})
        by_name = {r["id"].rsplit(".", 1)[-1]: r["outcome"] for r in records}
        self.assertEqual(by_name["test_ok"], test_report.PASSED)
        self.assertEqual(by_name["test_bad"], test_report.FAILED)


# -- D5: matrix join ---------------------------------------------------------


class TestMatrixJoin(unittest.TestCase):
    def test_unsupported_with_passing_refusal_is_unsupported(self):
        entries = [
            {
                "id": "run --weird",
                "kind": "option",
                "status": "UNSUPPORTED",
                "evidence": "fake",
                "test": "syn_refuse.Case0.test_refused",
            }
        ]
        outcomes = {"syn_refuse.Case0.test_refused": "passed"}
        tiers = {"syn_refuse.Case0.test_refused": "fake"}
        records = test_report.join_matrix(entries, outcomes, tiers)
        self.assertEqual(records[0]["outcome"], test_report.UNSUPPORTED)
        self.assertEqual(records[0]["reached"], "fake")
        self.assertFalse(records[0]["flagged"])

    def test_claimed_live_with_fake_only_pass_is_flagged(self):
        """A claim stronger than the evidence actually produced is flagged."""
        entries = [
            {
                "id": "docker ps",
                "kind": "command",
                "status": "SUPPORTED",
                "evidence": "live",
                "test": "syn_fakeonly.Case0.test_ps",
            }
        ]
        outcomes = {"syn_fakeonly.Case0.test_ps": "passed"}
        tiers = {"syn_fakeonly.Case0.test_ps": "fake"}
        records = test_report.join_matrix(entries, outcomes, tiers)
        self.assertEqual(records[0]["claimed"], "live")
        self.assertEqual(records[0]["reached"], "fake")
        self.assertTrue(records[0]["flagged"])

    def test_claimed_live_not_run_is_flagged(self):
        """A live claim whose test never ran is flagged and reported not run."""
        entries = [
            {
                "id": "docker stats",
                "kind": "command",
                "status": "SUPPORTED",
                "evidence": "live",
                "test": "syn_live_nr.Case0.test_stats",
            }
        ]
        outcomes = {"syn_live_nr.Case0.test_stats": "not run"}
        tiers = {"syn_live_nr.Case0.test_stats": "live"}
        records = test_report.join_matrix(entries, outcomes, tiers)
        self.assertEqual(records[0]["reached"], None)
        self.assertTrue(records[0]["flagged"])
        self.assertEqual(records[0]["outcome"], test_report.NOT_RUN)

    def test_class_level_test_id_matches_methods(self):
        entries = [
            {
                "id": "docker version --format",
                "kind": "format",
                "status": "SUPPORTED",
                "evidence": "fake",
                "test": "syn_cls.Case0",
            }
        ]
        outcomes = {
            "syn_cls.Case0.test_one": "passed",
            "syn_cls.Case0.test_two": "passed",
        }
        tiers = {
            "syn_cls.Case0.test_one": "fake",
            "syn_cls.Case0.test_two": "fake",
        }
        records = test_report.join_matrix(entries, outcomes, tiers)
        self.assertEqual(len(records[0]["test_ids"]), 2)
        self.assertEqual(records[0]["reached"], "fake")
        self.assertFalse(records[0]["flagged"])


# -- exit status -------------------------------------------------------------


class TestExitStatus(unittest.TestCase):
    def test_failure_yields_exit_1(self):
        module = _make_module("syn_exit_fail", "unit", [("test_bad", "fail", "boom")])
        records = _run(module, {"unit", "fake"})
        self.assertEqual(test_report.compute_exit_status(records), 1)

    def test_not_run_does_not_fail(self):
        """Not-run and skipped tests do not fail the build."""
        module = _make_module(
            "syn_exit_ok",
            "live",
            [("test_a", "skip", _outcomes.LIVE_TIER_CANNOT_RUN_PREFIX + " down"),
             ("test_b", "skip", "ordinary")],
        )
        records = _run(module, {"unit", "fake", "live"})
        self.assertEqual(test_report.compute_exit_status(records), 0)

    def test_all_pass_yields_exit_0(self):
        module = _make_module("syn_exit_pass", "unit", [("test_ok", "pass", None)])
        records = _run(module, {"unit", "fake"})
        self.assertEqual(test_report.compute_exit_status(records), 0)


# -- release notes -----------------------------------------------------------


class TestReleaseNotes(unittest.TestCase):
    def _report(self, records, enabled):
        return test_report.build_report(enabled, records, [], "1.5.0")

    def test_release_notes_say_live_not_run(self):
        records = [
            {"id": "syn_u.Case0.test_a", "tier": "unit", "outcome": "passed"},
            {
                "id": "syn_l.Case0.test_a",
                "tier": "live",
                "outcome": "not run",
                "reason": "tier not enabled",
            },
        ]
        report = self._report(records, {"unit", "fake"})
        notes = test_report.render_release_notes(report, "1.5.0")
        self.assertIn("live tier: not run", notes)
        self.assertIn("unit tier: ran", notes)

    def test_release_notes_say_live_not_run_when_gate_precondition(self):
        """An enabled live tier whose gate failed is still 'not run' in notes."""
        records = [
            {
                "id": "syn_l2.Case0.test_a",
                "tier": "live",
                "outcome": "not run",
                "reason": _outcomes.LIVE_TIER_CANNOT_RUN_PREFIX + " down",
            }
        ]
        report = self._report(records, {"unit", "fake", "live"})
        notes = test_report.render_release_notes(report, "1.5.0")
        self.assertIn("live tier: not run", notes)

    def test_release_notes_state_supported_version(self):
        records = [{"id": "syn_u2.Case0.test_a", "tier": "unit", "outcome": "passed"}]
        report = self._report(records, {"unit", "fake"})
        notes = test_report.render_release_notes(report, "1.5.0")
        self.assertIn("1.5.0", notes)


if __name__ == "__main__":
    unittest.main()
