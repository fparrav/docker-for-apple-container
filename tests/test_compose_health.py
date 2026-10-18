from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from container_docker_shim import compose_health  # noqa: E402


class ParseDurationTests(unittest.TestCase):
    def test_single_units(self):
        self.assertEqual(compose_health.parse_duration("30s"), 30.0)
        self.assertEqual(compose_health.parse_duration("500ms"), 0.5)
        self.assertEqual(compose_health.parse_duration("2h"), 7200.0)
        self.assertEqual(compose_health.parse_duration("5m"), 300.0)

    def test_combinations(self):
        self.assertEqual(compose_health.parse_duration("1m30s"), 90.0)
        self.assertEqual(compose_health.parse_duration("1h2m3s"), 3723.0)

    def test_bare_numbers_are_seconds(self):
        self.assertEqual(compose_health.parse_duration(10), 10.0)
        self.assertEqual(compose_health.parse_duration(1.5), 1.5)
        self.assertEqual(compose_health.parse_duration("10"), 10.0)

    def test_invalid_values_raise(self):
        for value in ("", "abc", "-5s", "10x", None, True, -1, "30s1m"):
            with self.assertRaises(ValueError, msg=repr(value)):
                compose_health.parse_duration(value)


class ParseHealthcheckTests(unittest.TestCase):
    def test_none_gives_none(self):
        self.assertIsNone(compose_health.parse_healthcheck(None))

    def test_defaults(self):
        result = compose_health.parse_healthcheck({"test": ["CMD", "true"]})
        self.assertIsInstance(result, compose_health.Healthcheck)
        self.assertEqual(result.test, ["CMD", "true"])
        self.assertEqual(result.interval, 30.0)
        self.assertEqual(result.timeout, 30.0)
        self.assertEqual(result.retries, 3)
        self.assertEqual(result.start_period, 0.0)
        self.assertEqual(result.start_interval, 5.0)
        self.assertFalse(result.disabled)

    def test_explicit_fields_with_duration_strings(self):
        result = compose_health.parse_healthcheck(
            {
                "test": ["CMD", "curl", "-f", "http://localhost/healthz"],
                "interval": "10s",
                "timeout": "2s",
                "retries": 5,
                "start_period": "1m30s",
                "start_interval": "250ms",
            }
        )
        self.assertEqual(result.test, ["CMD", "curl", "-f", "http://localhost/healthz"])
        self.assertEqual(result.interval, 10.0)
        self.assertEqual(result.timeout, 2.0)
        self.assertEqual(result.retries, 5)
        self.assertEqual(result.start_period, 90.0)
        self.assertEqual(result.start_interval, 0.25)
        self.assertFalse(result.disabled)

    def test_string_test_becomes_cmd_shell(self):
        result = compose_health.parse_healthcheck(
            {"test": "curl -f http://localhost/ || exit 1"}
        )
        self.assertEqual(
            result.test, ["CMD-SHELL", "curl -f http://localhost/ || exit 1"]
        )

    def test_list_test_kept_as_given(self):
        result = compose_health.parse_healthcheck(
            {"test": ["CMD-SHELL", "wc -c < /dev/null"]}
        )
        self.assertEqual(result.test, ["CMD-SHELL", "wc -c < /dev/null"])

    def test_none_test_disables(self):
        result = compose_health.parse_healthcheck({"test": ["NONE"]})
        self.assertEqual(result.test, ["NONE"])
        self.assertTrue(result.disabled)

    def test_disable_true_disables(self):
        result = compose_health.parse_healthcheck({"disable": True})
        self.assertTrue(result.disabled)

    def test_mapping_without_test_or_disable_raises(self):
        with self.assertRaises(ValueError):
            compose_health.parse_healthcheck({"interval": "10s"})

    def test_negative_retries_raise(self):
        with self.assertRaises(ValueError):
            compose_health.parse_healthcheck({"test": ["CMD", "true"], "retries": -1})

    def test_non_integer_retries_raise(self):
        with self.assertRaises(ValueError):
            compose_health.parse_healthcheck({"test": ["CMD", "true"], "retries": 2.5})


class RunCheckOnceTests(unittest.TestCase):
    def _check(self, test, disabled=False):
        return compose_health.Healthcheck(
            test=test,
            interval=30.0,
            timeout=30.0,
            retries=3,
            start_period=0.0,
            start_interval=5.0,
            disabled=disabled,
        )

    def test_cmd_argv_and_success(self):
        seen = []

        def exec_fn(argv):
            seen.append(argv)
            return types.SimpleNamespace(
                returncode=0, stdout="all good", stderr=""
            )

        ok, output = compose_health.run_check_once(
            exec_fn,
            "c1",
            self._check(["CMD", "pg_isready", "-U", "postgres"]),
        )
        self.assertEqual(
            seen, [["exec", "c1", "pg_isready", "-U", "postgres"]]
        )
        self.assertEqual((ok, output), (True, "all good"))

    def test_cmd_shell_argv(self):
        seen = []

        def exec_fn(argv):
            seen.append(argv)
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")

        ok, output = compose_health.run_check_once(
            exec_fn,
            "c1",
            self._check(["CMD-SHELL", "curl -f http://localhost/ || exit 1"]),
        )
        self.assertEqual(
            seen,
            [["exec", "c1", "sh", "-c", "curl -f http://localhost/ || exit 1"]],
        )
        self.assertEqual((ok, output), (True, ""))

    def test_nonzero_exit_combines_and_strips_output(self):
        def exec_fn(argv):
            return types.SimpleNamespace(
                returncode=1, stdout="  out-line\n", stderr="err-line\n"
            )

        ok, output = compose_health.run_check_once(
            exec_fn, "c1", self._check(["CMD", "true"])
        )
        self.assertEqual((ok, output), (False, "out-line\nerr-line"))

    def test_exec_oserror_reports_message(self):
        def exec_fn(argv):
            raise OSError("boom")

        ok, output = compose_health.run_check_once(
            exec_fn, "c1", self._check(["CMD", "true"])
        )
        self.assertEqual((ok, output), (False, "boom"))

    def test_output_trimmed_to_last_2000(self):
        long_stdout = "x" * 2500 + "TAIL"

        def exec_fn(argv):
            return types.SimpleNamespace(
                returncode=0, stdout=long_stdout, stderr=""
            )

        ok, output = compose_health.run_check_once(
            exec_fn, "c1", self._check(["CMD", "true"])
        )
        self.assertTrue(ok)
        self.assertEqual(len(output), 2000)
        self.assertEqual(output, long_stdout[-2000:])
        self.assertTrue(output.endswith("TAIL"))

    def test_value_errors(self):
        for check in (
            self._check(["CMD", "true"], disabled=True),
            self._check([]),
            self._check(["FOO", "x"]),
        ):
            seen = []

            def exec_fn(argv):
                seen.append(argv)
                return types.SimpleNamespace(
                    returncode=0, stdout="", stderr=""
                )

            with self.assertRaises(ValueError):
                compose_health.run_check_once(exec_fn, "c1", check)
            self.assertEqual(seen, [])


class FakeClock:
    """Virtual clock for ``wait_healthy`` tests: sleep advances now only."""

    def __init__(self, start=1000.0):
        self.now = start
        self.sleeps = []

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds
        self.sleeps.append(seconds)


class WaitHealthyTests(unittest.TestCase):
    def _check(self, **overrides):
        fields = dict(
            test=["CMD", "true"],
            interval=10.0,
            timeout=5.0,
            retries=2,
            start_period=0.0,
            start_interval=5.0,
            disabled=False,
        )
        fields.update(overrides)
        return compose_health.Healthcheck(**fields)

    def _check_once(self, results):
        pending = list(results)
        calls = []

        def check_once():
            if pending:
                result = pending.pop(0)
            else:
                result = calls[-1]
            calls.append(result)
            return result

        return check_once, calls

    def _state(self, sequence=()):
        queue = list(sequence)
        last = "running"

        def state():
            nonlocal last
            if queue:
                last = queue.pop(0)
            return last

        return state

    def _wait(self, check, fake, results, sequence=(), timeout=None):
        check_once, calls = self._check_once(results)
        result = compose_health.wait_healthy(
            check_once,
            check,
            self._state(sequence),
            fake.clock,
            fake.sleep,
            timeout=timeout,
        )
        return result, calls

    def test_success_on_first_check(self):
        fake = FakeClock()
        result, calls = self._wait(self._check(retries=2), fake, [(True, "up")])
        self.assertEqual(result.status, "healthy")
        self.assertEqual(result.attempts, 1)
        self.assertEqual(result.last_output, "up")
        self.assertEqual(len(calls), 1)
        self.assertEqual(fake.sleeps, [])

    def test_failures_inside_start_period_do_not_count(self):
        fake = FakeClock()
        check = self._check(
            start_period=30.0, start_interval=5.0, interval=10.0, retries=2
        )
        results = [
            (False, "fail-1"),
            (False, "fail-2"),
            (False, "fail-3"),
            (True, "up"),
        ]
        result, calls = self._wait(check, fake, results)
        self.assertEqual(result.status, "healthy")
        self.assertEqual(result.attempts, 4)
        self.assertEqual(result.last_output, "up")
        self.assertEqual(len(calls), 4)
        self.assertEqual(fake.sleeps, [5.0, 5.0, 5.0])

    def test_retry_exhaustion_returns_unhealthy(self):
        fake = FakeClock()
        check = self._check(start_period=0.0, interval=10.0, retries=3)
        result, calls = self._wait(check, fake, [(False, "fail")])
        self.assertEqual(result.status, "unhealthy")
        self.assertEqual(result.attempts, 3)
        self.assertEqual(result.last_output, "fail")
        self.assertEqual(len(calls), 3)
        self.assertEqual(fake.sleeps, [10.0, 10.0])

    def test_exited_container_stops_before_next_check(self):
        fake = FakeClock()
        check = self._check(interval=10.0, retries=10)
        result, calls = self._wait(
            check, fake, [(False, "fail")], sequence=("running", "exited")
        )
        self.assertEqual(result.status, "exited")
        self.assertEqual(result.attempts, 1)
        self.assertEqual(result.last_output, "fail")
        self.assertEqual(len(calls), 1)
        self.assertEqual(fake.sleeps, [10.0])

    def test_explicit_timeout_clips_sleeps_to_deadline(self):
        fake = FakeClock()
        check = self._check(interval=10.0, retries=10)
        result, calls = self._wait(check, fake, [(False, "fail")], timeout=25)
        self.assertEqual(result.status, "timeout")
        self.assertEqual(result.attempts, 3)
        self.assertEqual(result.last_output, "fail")
        self.assertEqual(len(calls), 3)
        self.assertEqual(fake.sleeps, [10.0, 10.0, 5.0])
        self.assertLessEqual(fake.now - 1000.0, 25.0)

    def test_pass_after_failures_is_healthy_unless_retries_ran_out(self):
        for retries, status, attempts in ((3, "healthy", 3), (2, "unhealthy", 2)):
            fake = FakeClock()
            check = self._check(start_period=0.0, interval=10.0, retries=retries)
            results = [
                (False, "fail-1"),
                (False, "fail-2"),
                (True, "up"),
            ]
            result, calls = self._wait(check, fake, results)
            self.assertEqual(result.status, status)
            self.assertEqual(result.attempts, attempts)
            self.assertEqual(len(calls), attempts)

    def test_sleep_is_clipped_at_the_end_of_start_period(self):
        fake = FakeClock()
        check = self._check(
            start_period=7.0, start_interval=5.0, interval=10.0, retries=2
        )
        result, calls = self._wait(check, fake, [(False, "fail")])
        self.assertEqual(result.status, "unhealthy")
        self.assertEqual(result.attempts, 4)
        self.assertEqual(fake.sleeps, [5.0, 2.0, 10.0])

    def test_retries_zero_still_runs_one_attempt(self):
        fake = FakeClock()
        check = self._check(retries=0, interval=10.0)
        result, calls = self._wait(check, fake, [(False, "fail")])
        self.assertEqual(result.status, "unhealthy")
        self.assertEqual(result.attempts, 1)
        self.assertEqual(result.last_output, "fail")
        self.assertEqual(len(calls), 1)
        self.assertEqual(fake.sleeps, [])


if __name__ == "__main__":
    unittest.main()
