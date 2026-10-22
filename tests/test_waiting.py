"""Unit tests for ``tests/waiting.py``: a fake clock keeps them instant."""

from __future__ import annotations

import unittest
from pathlib import Path

import waiting

TIER = "unit"


class FakeTime:
    """A clock that only moves when ``sleep`` is called."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list = []

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


class WaitUntilTests(unittest.TestCase):
    def setUp(self) -> None:
        self.time = FakeTime()

    def wait(self, check, timeout=10, describe="the thing", **kwargs):
        return waiting.wait_until(
            check, timeout, describe, clock=self.time.clock, sleep=self.time.sleep, **kwargs
        )

    def test_returns_the_first_truthy_value_without_sleeping(self) -> None:
        self.assertEqual(self.wait(lambda: "ready"), "ready")
        self.assertEqual(self.time.sleeps, [])

    def test_retries_until_the_check_holds(self) -> None:
        answers = iter([False, None, 0, "up"])
        self.assertEqual(self.wait(lambda: next(answers), interval=2), "up")
        self.assertEqual(self.time.sleeps, [2, 2, 2])

    def test_a_timeout_fails_with_the_description_and_the_last_state(self) -> None:
        with self.assertRaises(AssertionError) as raised:
            self.wait(lambda: "", timeout=3, describe=lambda: "db was not ready")
        text = str(raised.exception)
        self.assertIn("db was not ready", text)
        self.assertIn("within 3s", text)
        self.assertIn("last state: ''", text)

    def test_a_falsy_check_without_state_reports_no_state(self) -> None:
        with self.assertRaises(AssertionError) as raised:
            self.wait(lambda: False, timeout=2, describe="port 80 answering")
        self.assertEqual(str(raised.exception), "port 80 answering within 2s")

    def test_the_reported_state_is_the_one_from_the_last_attempt(self) -> None:
        attempts = []

        def check():
            attempts.append(len(attempts))
            return 0 * attempts[-1]

        with self.assertRaises(AssertionError) as raised:
            self.wait(check, timeout=2)
        self.assertEqual(attempts, [0, 1, 2])  # at t=0, t=1 and t=2
        self.assertIn("last state: 0", str(raised.exception))

    def test_the_check_runs_once_even_with_a_zero_timeout(self) -> None:
        calls = []
        with self.assertRaises(AssertionError):
            self.wait(lambda: calls.append(1), timeout=0)
        self.assertEqual(calls, [1])
        self.assertEqual(self.time.sleeps, [])

    def test_the_last_sleep_never_overshoots_the_deadline(self) -> None:
        with self.assertRaises(AssertionError):
            self.wait(lambda: False, timeout=2.5, interval=1)
        self.assertEqual(self.time.sleeps, [1, 1, 0.5])
        self.assertEqual(self.time.now, 2.5)

    def test_the_description_is_only_built_on_failure(self) -> None:
        def explode() -> str:
            raise AssertionError("describe must not run on success")

        self.assertTrue(self.wait(lambda: True, describe=explode))

    def test_a_plain_string_description_is_accepted(self) -> None:
        with self.assertRaises(AssertionError) as raised:
            self.wait(lambda: False, timeout=1, describe="a log line")
        self.assertIn("a log line", str(raised.exception))

    def test_an_exception_from_the_check_propagates(self) -> None:
        def broken():
            raise RuntimeError("runtime gone")

        with self.assertRaisesRegex(RuntimeError, "runtime gone"):
            self.wait(broken)


class LiveTierWaitsForConditionsTests(unittest.TestCase):
    """No live or conformance fixture decides a verdict with a fixed delay (design D9)."""

    def test_the_fixture_modules_never_call_time_sleep(self) -> None:
        for name in ("test_live_fixtures.py", "test_conformance_fixtures.py"):
            with self.subTest(module=name):
                text = (Path(__file__).resolve().parent / name).read_text(encoding="utf-8")
                self.assertNotIn("time.sleep", text)

    def test_postgres_readiness_is_asked_of_the_tcp_listener(self) -> None:
        for name in ("test_live_fixtures.py", "test_conformance_fixtures.py"):
            with self.subTest(module=name):
                text = (Path(__file__).resolve().parent / name).read_text(encoding="utf-8")
                self.assertIn('"pg_isready", "-h", "127.0.0.1"', text)


if __name__ == "__main__":
    unittest.main()
