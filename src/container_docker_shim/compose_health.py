"""Compose `healthcheck` parsing: durations and the disabled form.

Durations accept Go-style units (``30s``, ``1m30s``, ``500ms``, ``2h``,
``1h2m3s``) or bare numbers, which are seconds.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


DEFAULT_INTERVAL = 30.0
DEFAULT_TIMEOUT = 30.0
DEFAULT_RETRIES = 3
DEFAULT_START_PERIOD = 0.0
DEFAULT_START_INTERVAL = 5.0


# Go-style duration part: a number followed by one of the four units, and
# the only valid order of combinable units is h, m, s, ms.
_DURATION_PART = r"(\d+(?:\.\d+)?)(ms|s|m|h)"
_PART = re.compile(_DURATION_PART)
_DURATION = re.compile(r"(?:%s)+" % _DURATION_PART)
_BARE_NUMBER = re.compile(r"(?:\d+(?:\.\d+)?|\.\d+)")

_SECONDS = {"h": 3600.0, "m": 60.0, "s": 1.0, "ms": 0.001}
_ORDER = {"h": 0, "m": 1, "s": 2, "ms": 3}


@dataclass(frozen=True)
class Healthcheck:
    """A parsed `healthcheck`; all durations are seconds as floats."""

    test: list
    interval: float
    timeout: float
    retries: int
    start_period: float
    start_interval: float
    disabled: bool


def parse_duration(value) -> float:
    """Seconds for ``value``: Go-style units, or a bare number of seconds.

    Unit strings must use ``h``, ``m``, ``s``, ``ms`` in that order (any
    subset, e.g. ``1m30s``). Bare numbers -- int, float, or a numeric
    string -- are seconds. Anything else raises ``ValueError``.
    """
    if isinstance(value, bool):
        raise ValueError(
            "invalid duration %r: expected a duration string or a "
            "non-negative number of seconds" % (value,)
        )
    if isinstance(value, (int, float)):
        seconds = float(value)
        if seconds < 0:
            raise ValueError(
                "invalid duration %r: must be non-negative" % (value,)
            )
        return seconds
    if not isinstance(value, str):
        raise ValueError(
            "invalid duration %r: expected a duration string or a "
            "non-negative number of seconds" % (value,)
        )
    text = value.strip()
    if _BARE_NUMBER.fullmatch(text):
        return float(text)
    if not _DURATION.fullmatch(text):
        raise ValueError(
            "invalid duration %r: expected a Go-style duration such as "
            "30s, 1m30s, 500ms or 2h, or a number of seconds" % (value,)
        )
    total = 0.0
    previous = -1
    for amount, unit in _PART.findall(text):
        if _ORDER[unit] < previous:
            raise ValueError(
                "invalid duration %r: units must appear in the order "
                "h, m, s, ms" % (value,)
            )
        previous = _ORDER[unit]
        total += float(amount) * _SECONDS[unit]
    return total


def parse_healthcheck(spec) -> Healthcheck | None:
    """Build a ``Healthcheck`` from the compose ``healthcheck`` mapping.

    ``None`` means the service has no healthcheck. A mapping needs a
    ``test`` unless ``disable`` is true; a string test becomes
    ``["CMD-SHELL", <string>]``, ``["NONE"]`` disables, and duration keys
    go through :func:`parse_duration`. Missing keys take the compose
    defaults (30s interval, 30s timeout, 3 retries, 0s start period, 5s
    start interval).
    """
    if spec is None:
        return None
    if not isinstance(spec, dict):
        raise ValueError(
            "healthcheck must be a mapping, got %s" % type(spec).__name__
        )

    disabled = bool(spec.get("disable", False))
    test = spec.get("test")
    if isinstance(test, str):
        test = ["CMD-SHELL", test]
    elif isinstance(test, list):
        pass
    elif test is None:
        test = []
    else:
        raise ValueError(
            "healthcheck.test must be a string or a list, got %s"
            % type(test).__name__
        )
    if len(test) == 1 and str(test[0]).upper() == "NONE":
        disabled = True

    retries = spec.get("retries", DEFAULT_RETRIES)
    if isinstance(retries, bool) or not isinstance(retries, int) or retries < 0:
        raise ValueError(
            "healthcheck.retries must be a non-negative integer, got %r"
            % (retries,)
        )
    if test == [] and not disabled:
        raise ValueError("healthcheck requires a test")

    return Healthcheck(
        test=test,
        interval=parse_duration(spec.get("interval", DEFAULT_INTERVAL)),
        timeout=parse_duration(spec.get("timeout", DEFAULT_TIMEOUT)),
        retries=retries,
        start_period=parse_duration(spec.get("start_period", DEFAULT_START_PERIOD)),
        start_interval=parse_duration(
            spec.get("start_interval", DEFAULT_START_INTERVAL)
        ),
        disabled=disabled,
    )


# Most recent output wins: results are kept to their last 2000 characters.
_OUTPUT_TAIL = 2000


def run_check_once(exec_fn, container, check) -> tuple[bool, str]:
    """Run one healthcheck ``exec`` via ``exec_fn`` and report the result.

    ``exec_fn`` receives the full container-CLI argument list
    (``["exec", container, *command]``) and returns an object with
    ``returncode``, ``stdout`` and ``stderr`` (strings). A ``CMD`` test
    runs its items directly; a ``CMD-SHELL`` test wraps them in
    ``sh -c`` (several items are joined with a single space). A disabled
    check, an empty test, or any other first item is a programming error
    and raises ``ValueError``. Returns ``(True, output)`` when the exit
    code is zero, else ``(False, output)``; ``output`` is ``stdout`` +
    ``stderr`` stripped and trimmed to its last ``_OUTPUT_TAIL``
    characters. An ``OSError`` raised by ``exec_fn`` is reported as a
    failed check: ``(False, str(exc))``. No process is spawned here; the
    caller owns execution.
    """
    if check.disabled:
        raise ValueError(
            "run_check_once called with a disabled healthcheck; caller "
            "must skip it: %r" % (check,)
        )
    if not check.test:
        raise ValueError(
            "run_check_once called with an empty healthcheck test: %r"
            % (check,)
        )
    kind = check.test[0]
    if kind == "CMD":
        command = list(check.test[1:])
    elif kind == "CMD-SHELL":
        command = ["sh", "-c", " ".join(check.test[1:])]
    else:
        raise ValueError(
            "run_check_once got an unsupported test form %r "
            "(expected CMD or CMD-SHELL)" % (kind,)
        )
    try:
        result = exec_fn(["exec", container, *command])
    except OSError as exc:
        return False, str(exc)
    output = str(result.stdout) + str(result.stderr)
    return result.returncode == 0, output.strip()[-_OUTPUT_TAIL:]


@dataclass(frozen=True)
class WaitResult:
    """Outcome of :func:`wait_healthy`.

    ``status`` is one of ``"healthy"`` (a check passed),
    ``"unhealthy"`` (``retries`` consecutive counted failures),
    ``"exited"`` (the container stopped) or ``"timeout"`` (the deadline
    passed with no pass). ``attempts`` is the number of checks that ran
    and ``last_output`` is the output of the last one (``""`` if none
    ran).
    """

    status: str
    attempts: int
    last_output: str


def wait_healthy(check_once, check, state, clock, sleep, timeout=None) -> WaitResult:
    """Wait for a healthcheck to pass, or for a terminal status.

    ``check_once`` takes no arguments and returns ``(ok, output)``; the
    caller binds :func:`run_check_once`. ``state`` returns ``"running"``
    or ``"exited"``. ``clock`` returns the current time in seconds and
    ``sleep`` waits for a number of seconds; both are injected, so this
    module never imports ``time``.

    Returns ``WaitResult("healthy", ...)`` on the first passing check
    (a pass inside ``start_period`` also wins),
    ``WaitResult("unhealthy", ...)`` once ``check.retries`` consecutive
    failures are counted (a failure that finishes before
    ``check.start_period`` elapses does not count; ``retries == 0``
    means the first counted failure is unhealthy),
    ``WaitResult("exited", ...)`` as soon as ``state`` reports the
    container stopped, and ``WaitResult("timeout", ...)`` once the
    deadline passes. The deadline is ``timeout`` when given, else
    ``check.start_period + check.retries * (check.interval +
    check.timeout)``, floored at ``check.interval`` so a
    ``retries == 0`` check still gets one attempt. Never sleeps past
    the deadline.
    """
    start = clock()
    deadline_seconds = timeout if timeout is not None else max(
        check.start_period + check.retries * (check.interval + check.timeout),
        check.interval,
    )
    attempts = 0
    last_output = ""
    consecutive_failures = 0
    while True:
        if state() == "exited":
            return WaitResult("exited", attempts, last_output)
        if clock() - start >= deadline_seconds:
            return WaitResult("timeout", attempts, last_output)
        ok, output = check_once()
        attempts += 1
        last_output = output
        if ok:
            return WaitResult("healthy", attempts, last_output)
        if clock() - start >= check.start_period:
            consecutive_failures += 1
            if consecutive_failures >= check.retries:
                return WaitResult("unhealthy", attempts, last_output)
        elapsed = clock() - start
        if elapsed < check.start_period:
            # Do not sleep across the end of start_period: probe again right
            # when it ends so the regular interval takes over from there.
            wait = min(check.start_interval, check.start_period - elapsed)
        else:
            wait = check.interval
        wait = min(wait, max(deadline_seconds - elapsed, 0.0))
        if wait > 0:
            sleep(wait)
