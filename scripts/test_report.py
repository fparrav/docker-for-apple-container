#!/usr/bin/env python3
"""Run the tiered test suite and produce a five-outcome compatibility report.

The suite is organised into tiers (``tests/tiers.py``): ``unit`` and ``fake``
always run, while ``live`` and ``conformance`` are opt-in. This script runs the
enabled tiers with a ``unittest`` result collector that records, per test, one
of the five outcomes of design D4 and the test's tier, then joins the results
with the compatibility matrix (design D5) so every claim is traced to the
strongest evidence actually produced in the run.

The five outcomes:

* ``passed``     the test ran and succeeded.
* ``failed``     the test raised an assertion error or exception.
* ``skipped``    the test declined to run for a stated, test-level reason.
* ``not run``    the tier was not enabled, or it was enabled but its gate
                 precondition failed (the test was never started).
* ``unsupported`` a matrix entry whose status is UNSUPPORTED and whose refusal
                 test passed (an entry-level outcome, joined from the matrix).

A tier that was not enabled is reported as ``not run`` for all of its tests,
never as skipped or passed. A skip whose reason matches a tier-gate
precondition (the shared constants in ``tests/_outcomes.py``) is reclassified
as ``not run``; any other skip stays ``skipped``.

Output: a machine-readable ``report.json`` and a Markdown summary (written to
``$GITHUB_STEP_SUMMARY`` when set, otherwise printed). ``--release-notes``
prints release-note text instead of the summary. The exit status is non-zero
iff any test failed; ``not run`` and flagged claims are reported, not failing.

Usage:
    scripts/test_report.py                     run unit+fake (plus $SHIM_TIERS)
    scripts/test_report.py --tiers unit,fake   run exactly the named tiers
    scripts/test_report.py --release-notes     print release notes
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TESTS_DIR = ROOT / "tests"
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

import _outcomes  # noqa: E402

# -- the five outcomes -------------------------------------------------------

PASSED = "passed"
FAILED = "failed"
SKIPPED = "skipped"
NOT_RUN = "not run"
UNSUPPORTED = "unsupported"
OUTCOMES = (PASSED, FAILED, SKIPPED, NOT_RUN, UNSUPPORTED)

# A test is never "unsupported" (that is entry-level); it has these four.
TEST_OUTCOMES = (PASSED, FAILED, SKIPPED, NOT_RUN)

# Evidence levels, ordered weakest to strongest (D5 ranking).
EVIDENCE = ("unit", "fake", "live", "conformance")
EVIDENCE_RANK = {level: rank for rank, level in enumerate(EVIDENCE)}

# Tiers that always run; the opt-in tiers run only when requested.
DEFAULT_TIERS = ("unit", "fake")
OPT_IN_TIERS = ("live", "conformance")
ALL_TIERS = DEFAULT_TIERS + OPT_IN_TIERS

TIERS_ENV = "SHIM_TIERS"
NOT_RUN_REASON = "tier not enabled"
MATRIX_PATH = TESTS_DIR / "compat" / "matrix.json"


# -- outcome classification --------------------------------------------------


def is_tier_gate_skip(tier: str, reason: str) -> bool:
    """Return True when a skip is a tier-gate precondition, not an ordinary skip.

    A tier-gate precondition means the tier (or its runtime) could not run, so
    the test is reclassified as ``not run``. Per-test preconditions (for
    example a missing local image) are ordinary skips and stay ``skipped``.
    The match uses the shared constants from ``tests/_outcomes.py`` so it is
    not text guessing.
    """
    if tier == "live":
        return reason == _outcomes.LIVE_TIER_DISABLED or reason.startswith(
            _outcomes.LIVE_TIER_CANNOT_RUN_PREFIX
        )
    if tier == "conformance":
        return (
            reason == _outcomes.CONFORMANCE_TIER_DISABLED
            or reason == _outcomes.CONFORMANCE_DOCKER_NOT_SET
            or reason.startswith(_outcomes.CONFORMANCE_TIER_CANNOT_RUN_PREFIX)
        )
    return False


def classify_skip(tier: str, reason: str) -> tuple:
    """Map a skip to ``(outcome, reason)``.

    A tier-gate precondition skip becomes ``not run``; any other skip stays
    ``skipped``. The original reason is preserved in both cases.
    """
    if is_tier_gate_skip(tier, reason):
        return (NOT_RUN, reason)
    return (SKIPPED, reason)


def module_tier(module) -> str:
    """The tier declared by a test module (its module-level ``TIER``)."""
    tier = getattr(module, "TIER", None)
    if isinstance(tier, str) and tier:
        return tier
    return "unit"


def _tier_of(test) -> str:
    """The tier of one test case, read from its defining module."""
    module_name = getattr(test, "__module__", None)
    if module_name:
        module = sys.modules.get(module_name)
        if module is not None:
            return module_tier(module)
    return "unit"


def _case_id(test) -> str:
    """The dotted id of one test case: ``module.Class.method``."""
    return "%s.%s.%s" % (
        type(test).__module__,
        type(test).__name__,
        test._testMethodName,
    )


def _err_reason(err) -> str:
    """A short human-readable reason for a failure/error ``(type, value, tb)``."""
    typ, value, _tb = err
    return "%s: %s" % (typ.__name__, value)


class CollectingResult(unittest.TestResult):
    """A ``unittest.TestResult`` that records one outcome per test.

    Each test is recorded with its module tier and one of the four test-level
    outcomes. Skips are reclassified through :func:`classify_skip` so a
    tier-gate precondition skip becomes ``not run`` rather than ``skipped``.
    """

    def __init__(self) -> None:
        super().__init__()
        self.records = []

    def _record(self, test, outcome: str, reason: str = None) -> None:
        entry = {"id": _case_id(test), "tier": _tier_of(test), "outcome": outcome}
        if reason is not None:
            entry["reason"] = reason
        self.records.append(entry)

    def addSuccess(self, test) -> None:
        super().addSuccess(test)
        self._record(test, PASSED)

    def addFailure(self, test, err) -> None:
        super().addFailure(test, err)
        self._record(test, FAILED, _err_reason(err))

    def addError(self, test, err) -> None:
        super().addError(test, err)
        self._record(test, FAILED, _err_reason(err))

    def addSkip(self, test, reason) -> None:
        super().addSkip(test, reason)
        outcome, r = classify_skip(_tier_of(test), reason)
        self._record(test, outcome, r)


# -- discovery and execution -------------------------------------------------


def discover_test_modules(tests_dir: Path = TESTS_DIR) -> dict:
    """Import every ``test_*.py`` in ``tests_dir`` and return ``{name: module}``."""
    modules = {}
    for path in sorted(tests_dir.glob("test_*.py")):
        modules[path.stem] = importlib.import_module(path.stem)
    return modules


def _iter_cases(suite) -> "iter":
    """Yield every ``TestCase`` in a (possibly nested) ``TestSuite``."""
    for item in suite:
        if isinstance(item, unittest.TestCase):
            yield item
        elif isinstance(item, unittest.TestSuite):
            yield from _iter_cases(item)


def run_suite(modules: dict, enabled_tiers, loader: unittest.TestLoader = None) -> list:
    """Run the enabled tiers and enumerate the disabled ones.

    Returns one record per test across every module. Tests in an enabled tier
    are executed and their real outcome recorded; tests in a disabled tier are
    enumerated (never started) and recorded as ``not run``.
    """
    loader = loader or unittest.TestLoader()
    result = CollectingResult()
    records = []
    result.startTestRun()
    try:
        for _name, module in modules.items():
            tier = module_tier(module)
            suite = loader.loadTestsFromModule(module)
            if tier in enabled_tiers:
                before = len(result.records)
                suite(result)
                records.extend(result.records[before:])
            else:
                for case in _iter_cases(suite):
                    records.append(
                        {
                            "id": _case_id(case),
                            "tier": tier,
                            "outcome": NOT_RUN,
                            "reason": NOT_RUN_REASON,
                        }
                    )
    finally:
        result.stopTestRun()
    return records


# -- matrix join (D5 evidence tracing) ---------------------------------------


def _rank(level) -> int:
    """The rank of an evidence level; unknown or absent levels rank lowest."""
    if level is None:
        return -1
    return EVIDENCE_RANK.get(level, -1)


def matching_test_ids(spec, all_ids) -> list:
    """Return the run's test ids that match a matrix entry's ``test`` spec.

    The spec is either a full ``module.Class.method`` id (exact match) or a
    ``module.Class`` id (matching every method of the class).
    """
    if not spec:
        return []
    matched = []
    for tid in all_ids:
        if tid == spec or tid.startswith(spec + "."):
            matched.append(tid)
    return matched


def highest_reached(matched, test_outcomes, test_tiers):
    """The strongest evidence level among the matched tests that passed."""
    best = None
    for tid in matched:
        if test_outcomes.get(tid) == PASSED:
            tier = test_tiers.get(tid)
            if best is None or _rank(tier) > _rank(best):
                best = tier
    return best


def entry_outcome(status, matched, test_outcomes) -> str:
    """Classify one matrix entry into exactly one of the five outcomes."""
    if not matched:
        return NOT_RUN
    outcomes = [test_outcomes.get(tid) for tid in matched]
    if FAILED in outcomes:
        return FAILED
    if status == "UNSUPPORTED" and PASSED in outcomes:
        return UNSUPPORTED
    if PASSED in outcomes:
        return PASSED
    if SKIPPED in outcomes:
        return SKIPPED
    return NOT_RUN


def join_matrix(entries, test_outcomes, test_tiers) -> list:
    """Join matrix entries with the run's test outcomes (design D5).

    For each entry the record carries the claimed evidence level, the strongest
    level actually produced by its passing tests, the test ids that produced
    it, one of the five outcomes, and whether the claim was not reached.
    """
    all_ids = list(test_outcomes)
    records = []
    for entry in entries:
        spec = entry.get("test")
        matched = matching_test_ids(spec, all_ids)
        claimed = entry.get("evidence")
        reached = highest_reached(matched, test_outcomes, test_tiers)
        records.append(
            {
                "id": entry.get("id"),
                "kind": entry.get("kind"),
                "status": entry.get("status"),
                "claimed": claimed,
                "reached": reached,
                "test_ids": matched,
                "outcome": entry_outcome(entry.get("status"), matched, test_outcomes),
                "flagged": _rank(reached) < _rank(claimed),
            }
        )
    return records


# -- report assembly ---------------------------------------------------------


def _tier_status(enabled: bool, counts: dict) -> str:
    """The effective status of a tier: ``ran`` or ``not run``.

    A tier that was not enabled is ``not run``. An enabled tier is ``ran`` only
    if at least one of its tests actually ran; if every test was reclassified
    as ``not run`` (its gate precondition failed) the tier is ``not run``.
    """
    if not enabled:
        return NOT_RUN
    if any(counts[outcome] for outcome in (PASSED, FAILED, SKIPPED)):
        return "ran"
    return NOT_RUN


def build_report(enabled_tiers, test_records, matrix_entries, supported_version=None) -> dict:
    """Assemble the full report from the run's test records and the matrix."""
    tiers = {}
    for tier in ALL_TIERS:
        enabled = tier in enabled_tiers
        counts = {outcome: 0 for outcome in TEST_OUTCOMES}
        for rec in test_records:
            if rec["tier"] == tier:
                counts[rec["outcome"]] += 1
        info = {"enabled": enabled, "counts": counts}
        info["status"] = _tier_status(enabled, counts)
        if info["status"] == NOT_RUN:
            info["reason"] = (
                NOT_RUN_REASON if not enabled else "tier precondition not met"
            )
        tiers[tier] = info

    test_outcomes = {rec["id"]: rec["outcome"] for rec in test_records}
    test_tiers = {rec["id"]: rec["tier"] for rec in test_records}
    matrix_records = join_matrix(matrix_entries, test_outcomes, test_tiers)
    matrix_counts = {outcome: 0 for outcome in OUTCOMES}
    for rec in matrix_records:
        matrix_counts[rec["outcome"]] += 1

    return {
        "generated": datetime.now(timezone.utc).isoformat(),
        "supported_version": supported_version,
        "tiers": tiers,
        "tests": test_records,
        "matrix": {
            "entries": matrix_records,
            "counts": matrix_counts,
            "flagged": [rec["id"] for rec in matrix_records if rec["flagged"]],
        },
    }


# -- rendering ---------------------------------------------------------------


def render_markdown(report: dict) -> str:
    """Render the human-readable Markdown summary of a report."""
    lines = ["# Test report", ""]
    version = report.get("supported_version")
    lines.append(
        "Supported Apple container release: %s" % (version or "unknown")
    )
    lines.append("")
    lines.append("## Tier outcomes")
    lines.append("")
    lines.append("| Tier | Status | Passed | Failed | Skipped | Not run |")
    lines.append("| --- | --- | --- | --- | --- | --- |")
    for tier in ALL_TIERS:
        t = report["tiers"][tier]
        c = t["counts"]
        lines.append(
            "| %s | %s | %d | %d | %d | %d |"
            % (tier, t["status"], c[PASSED], c[FAILED], c[SKIPPED], c[NOT_RUN])
        )
    lines.append("")
    matrix = report["matrix"]
    mc = matrix["counts"]
    lines.append("## Matrix evidence")
    lines.append("")
    lines.append(
        "- %d entries: %d passed, %d failed, %d skipped, %d not run, %d unsupported"
        % (
            len(matrix["entries"]),
            mc[PASSED],
            mc[FAILED],
            mc[SKIPPED],
            mc[NOT_RUN],
            mc[UNSUPPORTED],
        )
    )
    if matrix["flagged"]:
        lines.append(
            "- %d entries flagged (claimed evidence not reached):"
            % len(matrix["flagged"])
        )
        for rec in matrix["entries"]:
            if rec["flagged"]:
                lines.append(
                    "  - `%s` — claimed %s, reached %s"
                    % (rec["id"], rec["claimed"], rec["reached"] or "none")
                )
    else:
        lines.append("- no entries flagged")
    lines.append("")
    return "\n".join(lines)


def render_release_notes(report: dict, supported_version: str = None) -> str:
    """Render release-note text stating the runtime and which tiers ran.

    A tier that did not run is stated as ``<tier> tier: not run`` so a release
    candidate never implies evidence that was not produced.
    """
    version = supported_version or report.get("supported_version")
    lines = []
    if version:
        lines.append("Supported Apple container release: %s" % version)
        lines.append("")
    lines.append("## Test tiers")
    for tier in ALL_TIERS:
        t = report["tiers"][tier]
        if t["status"] == NOT_RUN:
            lines.append("- %s tier: not run" % tier)
        else:
            c = t["counts"]
            lines.append(
                "- %s tier: ran (%d passed, %d failed, %d skipped, %d not run)"
                % (tier, c[PASSED], c[FAILED], c[SKIPPED], c[NOT_RUN])
            )
    matrix = report["matrix"]
    if matrix["flagged"]:
        lines.append("")
        lines.append("## Compatibility claims not reached in this run")
        lines.append("")
        for rec in matrix["entries"]:
            if rec["flagged"]:
                lines.append(
                    "- `%s`: claimed %s, reached %s"
                    % (rec["id"], rec["claimed"], rec["reached"] or "none")
                )
    return "\n".join(lines)


def compute_exit_status(test_records) -> int:
    """The process exit status: non-zero iff any test failed.

    ``not run`` and flagged claims are reported but do not fail the build.
    """
    return 1 if any(rec["outcome"] == FAILED for rec in test_records) else 0


# -- entry point -------------------------------------------------------------


def parse_tiers(raw: str = None) -> frozenset:
    """Parse a comma-separated tier list into a set (absent/empty -> empty)."""
    if raw is None:
        return frozenset()
    return frozenset(tier.strip().lower() for tier in raw.split(",") if tier.strip())


def enabled_tiers(cli_tiers: str = None) -> frozenset:
    """The tiers to run: ``unit``+``fake`` always, plus the requested opt-ins.

    ``cli_tiers`` (``--tiers``) wins over ``$SHIM_TIERS`` when given.
    """
    if cli_tiers is not None:
        requested = parse_tiers(cli_tiers)
    else:
        requested = parse_tiers(os.environ.get(TIERS_ENV, ""))
    return frozenset(DEFAULT_TIERS) | requested


def _supported_version():
    """The supported Apple ``container`` release, or ``None`` if unavailable."""
    src = ROOT / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))
    try:
        from container_docker_shim.cli import SUPPORTED_CONTAINER_VERSION

        return SUPPORTED_CONTAINER_VERSION
    except Exception:
        return None


def _load_matrix() -> list:
    """Load the compatibility matrix, or an empty list when unreadable."""
    try:
        with open(MATRIX_PATH, encoding="utf-8") as handle:
            entries = json.load(handle)
    except (OSError, ValueError):
        return []
    return entries if isinstance(entries, list) else []


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the tiered suite and write a five-outcome report"
    )
    parser.add_argument(
        "--tiers",
        default=None,
        help="comma-separated tiers to run (default: unit,fake plus $%s)" % TIERS_ENV,
    )
    parser.add_argument(
        "--report",
        default="report.json",
        help="path to write the JSON report (default: report.json)",
    )
    parser.add_argument(
        "--release-notes",
        action="store_true",
        help="print release notes instead of the Markdown summary",
    )
    args = parser.parse_args(argv)

    enabled = enabled_tiers(args.tiers)
    modules = discover_test_modules()
    records = run_suite(modules, enabled)
    report = build_report(enabled, records, _load_matrix(), _supported_version())

    report_path = Path(args.report)
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    output = (
        render_release_notes(report)
        if args.release_notes
        else render_markdown(report)
    )
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as handle:
            handle.write(output + "\n")
    else:
        sys.stdout.write(output + "\n")

    return compute_exit_status(records)


if __name__ == "__main__":
    raise SystemExit(main())
