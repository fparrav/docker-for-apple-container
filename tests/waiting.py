"""Polling helper shared by the live and conformance tiers.

A verdict that depends on readiness (a port answering, a database accepting
connections, a log line, a state change) is decided by waiting for the
condition, never by a fixed delay: ``wait_until`` returns on the first check
that holds and otherwise fails with what it was waiting for and the last state
it saw. The clock and the sleep are injectable so the unit test needs no real
time.
"""

from __future__ import annotations

import time
from typing import Any, Callable, Union

Description = Union[str, Callable[[], str]]


def wait_until(
    check: Callable[[], Any],
    timeout: float,
    describe: Description,
    *,
    interval: float = 1.0,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> Any:
    """Call ``check`` until it returns a truthy value and return that value.

    ``check`` runs at least once, also when ``timeout`` is 0, and an exception
    it raises propagates. After ``timeout`` seconds without a truthy result the
    call raises ``AssertionError`` carrying ``describe()`` (or ``describe``
    itself when it is a string), the timeout and, when ``check`` returned
    something other than ``False`` or ``None``, that last value.
    """
    deadline = clock() + timeout
    while True:
        last = check()
        if last:
            return last
        remaining = deadline - clock()
        if remaining <= 0:
            break
        sleep(min(interval, remaining))
    text = describe() if callable(describe) else describe
    message = f"{text} within {timeout:g}s"
    if last is not False and last is not None:
        message += f"; last state: {last!r}"
    raise AssertionError(message)
