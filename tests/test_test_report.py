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
import tempfile
import textwrap
import types
import unittest
from unittest.mock import patch
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
    def test_subtest_failure_is_recorded_as_failed(self):
        case = _make_module("syn_subtest_failure", "unit", [("test_a", "pass", None)]).Case0()
        result = test_report.CollectingResult()
        err = (AssertionError, AssertionError("subtest failed"), None)

        result.addSubTest(case, case, err)

        self.assertEqual(result.records[0]["id"], case.id())
        self.assertEqual(result.records[0]["outcome"], test_report.FAILED)

    def test_class_setup_skip_holder_can_be_recorded(self):
        holder = unittest.suite._ErrorHolder("syn_module.TestCase.setUpClass")
        result = test_report.CollectingResult()

        result.addSkip(holder, "optional dependency missing")

        self.assertEqual(result.records[0]["id"], "syn_module.TestCase.setUpClass")
        self.assertEqual(result.records[0]["outcome"], test_report.SKIPPED)

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
    def test_unreadable_matrix_is_not_treated_as_empty(self):
        with patch("builtins.open", side_effect=OSError("missing")):
            with self.assertRaisesRegex(ValueError, "cannot load compatibility matrix"):
                test_report._load_matrix()

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


# -- declared_tests: the static count behind --require ------------------------


class TestDeclaredTests(unittest.TestCase):
    def _scan(self, tier, **modules):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name, source in modules.items():
                (root / (name + ".py")).write_text(textwrap.dedent(source), encoding="utf-8")
            return test_report.declared_tests(tier, root)

    def test_counts_decorated_test_functions_per_tier(self):
        live = """
            TIER = "live"
            import tiers

            @tiers.live_tier()
            def test_a(): pass

            def test_b(): pass

            @tiers.live_tier(run=None)
            def helper(): pass
            """
        conformance = """
            TIER = "conformance"
            from tiers import conformance_tier

            @conformance_tier()
            def test_c(): pass
            """
        self.assertEqual(
            self._scan("live", test_one=live, test_two=conformance), ["test_one.test_a"]
        )
        self.assertEqual(
            self._scan("conformance", test_one=live, test_two=conformance),
            ["test_two.test_c"],
        )

    def test_class_methods_are_found(self):
        source = """
            TIER = "live"
            import tiers

            class Suite:
                @tiers.live_tier()
                def test_m(self): pass
            """
        self.assertEqual(self._scan("live", test_cls=source), ["test_cls.Suite.test_m"])

    def test_module_of_another_tier_is_ignored(self):
        source = """
            TIER = "unit"
            import tiers

            @tiers.live_tier()
            def test_throwaway(): pass
            """
        self.assertEqual(self._scan("live", test_unit=source), [])

    def test_unparseable_module_is_skipped(self):
        good = """
            TIER = "live"
            import tiers

            @tiers.live_tier()
            def test_ok(): pass
            """
        self.assertEqual(
            self._scan("live", test_bad="def (:\n", test_good=good), ["test_good.test_ok"]
        )

    def test_invalid_tier_raises(self):
        for tier in ("unit", "fake", "other"):
            with self.subTest(tier=tier):
                with self.assertRaises(ValueError):
                    test_report.declared_tests(tier)

    def test_the_repository_declares_tests_for_both_opt_in_tiers(self):
        self.assertGreater(len(test_report.declared_tests("live")), 0)
        self.assertGreater(len(test_report.declared_tests("conformance")), 0)


# -- --require: a tier that must run completely -----------------------------


def _rec(tier, name, outcome, reason=None):
    record = {"id": "m." + name, "tier": tier, "outcome": outcome}
    if reason is not None:
        record["reason"] = reason
    return record


def _declared(count):
    return lambda tier: ["m.t%d" % i for i in range(count)]


class TestUnmetRequirements(unittest.TestCase):
    def test_complete_tier_has_no_problem(self):
        records = [_rec("conformance", "a", "passed"), _rec("conformance", "b", "passed")]
        self.assertEqual(test_report.unmet_requirements(records, ["conformance"], _declared(2)), [])

    def test_not_run_tests_are_listed_with_their_reason(self):
        records = [_rec("conformance", "a", "not run", "tier not enabled")]
        problems = test_report.unmet_requirements(records, ["conformance"], _declared(1))
        self.assertTrue(any("m.a is not run (tier not enabled)" in p for p in problems))

    def test_skipped_test_is_named(self):
        records = [_rec("live", "a", "passed"), _rec("live", "b", "skipped", "no image")]
        problems = test_report.unmet_requirements(records, ["live"], _declared(2))
        self.assertTrue(any("m.b is skipped (no image)" in p for p in problems))

    def test_fewer_passed_than_declared_gives_both_counts(self):
        records = [_rec("conformance", "a", "passed")]
        problems = test_report.unmet_requirements(records, ["conformance"], _declared(3))
        self.assertIn("conformance: 3 declared in the sources, 1 passed", problems)

    def test_tier_without_declared_tests_is_unmet(self):
        problems = test_report.unmet_requirements([], ["live"], _declared(0))
        self.assertTrue(any("no test declares this tier" in p for p in problems))

    def test_other_tiers_do_not_count(self):
        records = [_rec("unit", "a", "passed"), _rec("live", "b", "passed")]
        problems = test_report.unmet_requirements(records, ["conformance"], _declared(1))
        self.assertIn("conformance: 1 declared in the sources, 0 passed", problems)

    def test_long_lists_are_summarised(self):
        records = [_rec("live", "t%d" % i, "not run", "off") for i in range(8)]
        problems = test_report.unmet_requirements(records, ["live"], _declared(8))
        self.assertIn("live: and 3 more not passed", problems)


class TestRequireFlag(unittest.TestCase):
    def _main(self, modules, declared, extra=()):
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "report.json"
            argv = ["--report", str(report), "--tiers", "conformance", *extra]
            with patch.object(test_report, "discover_test_modules", return_value=modules), \
                    patch.object(test_report, "declared_tests", declared), \
                    patch.dict("os.environ", {}, clear=False), \
                    patch("sys.stdout"), patch("sys.stderr"):
                status = test_report.main(argv)
            return status, report.exists()

    def test_unmet_requirement_exits_3_and_still_writes_the_report(self):
        module = _make_module("syn_req_skip", "conformance", [("test_a", "skip", "no engine")])
        status, written = self._main({module.__name__: module}, _declared(1), ["--require", "conformance"])
        self.assertEqual((status, written), (3, True))

    def test_met_requirement_exits_0(self):
        module = _make_module("syn_req_ok", "conformance", [("test_a", "pass", None)])
        status, _ = self._main({module.__name__: module}, _declared(1), ["--require", "conformance"])
        self.assertEqual(status, 0)

    def test_without_the_flag_a_skip_still_exits_0(self):
        module = _make_module("syn_req_off", "conformance", [("test_a", "skip", "no engine")])
        status, _ = self._main({module.__name__: module}, _declared(1))
        self.assertEqual(status, 0)

    def test_a_failure_keeps_exit_1(self):
        module = _make_module("syn_req_fail", "conformance", [("test_a", "fail", "boom")])
        status, _ = self._main({module.__name__: module}, _declared(1), ["--require", "conformance"])
        self.assertEqual(status, 1)

    def test_a_tier_that_is_not_enabled_is_unmet(self):
        module = _make_module("syn_req_disabled", "live", [("test_a", "pass", None)])
        status, _ = self._main({module.__name__: module}, _declared(1), ["--require", "live"])
        self.assertEqual(status, 3)

    def test_unit_cannot_be_required(self):
        with patch("sys.stderr"), self.assertRaises(SystemExit) as raised:
            test_report.main(["--require", "unit"])
        self.assertEqual(raised.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
