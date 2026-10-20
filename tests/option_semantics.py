"""Reviewed D1 audit rows for Docker/Apple CLI option semantics.

Each row is (command, option, docker_meaning, apple_meaning, decision,
evidence). The short-alias set is derived from cli.py by
test_option_semantics; D1's selected long-option risks are declared below.
"""

from __future__ import annotations

from typing import NamedTuple


class OptionSemantics(NamedTuple):
    command: str
    option: str
    docker_meaning: str
    apple_meaning: str
    decision: str
    evidence: str


# Explicit D1 set: conversions, dialect differences, options with a documented
# semantic mismatch, and options D1 calls out as refused by the default branch.
# This deliberately avoids treating every verbatim long option as high risk.
RISKY_PAIRS = frozenset(
    {
        ("run", "--cpu-shares"),
        ("run", "--cpus"),
        ("run", "--memory"),
        ("run", "--user"),
        ("run", "--cap-add"),
        ("run", "--cap-drop"),
        ("run", "--network"),
        ("run", "--volume"),
        ("run", "--mount"),
        ("run", "--runtime"),
        ("run", "--arch"),
        ("run", "--no-dns"),
        ("run", "--env-file"),
        ("run", "--pids-limit"),
        ("run", "--security-opt"),
        ("run", "--privileged"),
        ("run", "--device"),
        ("run", "--ipc"),
        ("run", "--pid"),
        ("run", "--group-add"),
        ("kill", "-s"),
        ("stop", "-s"),
        ("build", "--rm"),
        ("build", "--force-rm"),
        ("build", "--memory"),
        ("build", "--cpus"),
    }
)


ROWS = (
    # All current short aliases are recorded so a newly added alias needs review.
    OptionSemantics("run", "-c", "CPU weight shares", "integer CPU count", "refuse", "D1; C1"),
    OptionSemantics("run", "-d", "detach", "detach", "keep", "D1 short-alias audit"),
    OptionSemantics("run", "-e", "set environment variable", "set environment variable", "keep", "D1 short-alias audit"),
    OptionSemantics("run", "-i", "keep stdin open", "keep stdin open", "keep", "D1 short-alias audit"),
    OptionSemantics("run", "-it", "interactive TTY", "interactive TTY", "keep", "D1 short-alias audit"),
    OptionSemantics("run", "-l", "add label", "add label", "keep", "D1 short-alias audit"),
    OptionSemantics("run", "-m", "memory; bare value is bytes", "memory; 1 MiB granularity", "convert exact MiB multiples; refuse others", "D1; D2"),
    OptionSemantics("run", "-p", "publish port", "publish port", "keep", "D1 short-alias audit"),
    OptionSemantics("run", "-t", "allocate TTY", "allocate TTY", "keep", "D1 short-alias audit"),
    OptionSemantics("run", "-ti", "interactive TTY", "interactive TTY", "keep", "D1 short-alias audit"),
    OptionSemantics("run", "-u", "set uid/gid or user name", "set guest uid/gid or name", "keep", "D1; live evidence"),
    OptionSemantics("run", "-v", "bind mount or named volume", "bind mount; named volume translated", "translate named volume", "D1; S1"),
    OptionSemantics("run", "-w", "set working directory", "set working directory", "keep", "D1 short-alias audit"),
    OptionSemantics("build", "-c", "CPU weight shares", "integer CPU count", "refuse", "D1"),
    OptionSemantics("build", "-f", "select Dockerfile", "select build file", "keep", "D1 short-alias audit"),
    OptionSemantics("build", "-h", "show help", "show help", "keep", "D1 short-alias audit"),
    OptionSemantics("build", "-m", "builder memory limit", "builder memory limit", "keep; normalize units", "D1"),
    OptionSemantics("build", "-o", "select build output", "select build output", "keep", "D1 short-alias audit"),
    OptionSemantics("build", "-q", "quiet output", "quiet output", "keep", "D1 short-alias audit"),
    OptionSemantics("build", "-t", "tag image", "tag image", "keep", "D1 short-alias audit"),
    # D1 semantic-risk rows.
    OptionSemantics("run", "--cpu-shares", "CPU weight shares", "no equivalent; --cpus is a count", "refuse", "D1"),
    OptionSemantics("run", "--cpus", "fractional CFS quota", "integer CPU count", "refuse fractional values; forward integers", "D1; C1"),
    OptionSemantics("run", "--memory", "bare values are bytes", "bare values are MiB with 1 MiB granularity", "convert exact MiB multiples; refuse others", "D1; D2"),
    OptionSemantics("run", "--user", "uid/gid or user name", "guest uid/gid or name", "keep", "D1; live evidence"),
    OptionSemantics("run", "--cap-add", "add Linux capability", "add capability; names normalized", "keep", "D1"),
    OptionSemantics("run", "--cap-drop", "drop Linux capability", "drop capability; names normalized", "keep", "D1"),
    OptionSemantics("run", "--network", "Docker network mode or name", "named network; bridge/default omitted", "refuse unsupported modes; map or forward supported values", "D1; N1"),
    OptionSemantics("run", "--volume", "bind mount or named volume", "bind mount; named volume translated", "translate named volume", "D1; S1"),
    OptionSemantics("run", "--mount", "src/dst/ro; Docker volume type", "source/target/readonly; different volume support", "translate aliases; refuse unsupported forms", "D1; D4"),
    OptionSemantics("run", "--runtime", "select OCI runtime such as runc", "select Apple runtime handler", "refuse", "D1"),
    OptionSemantics("run", "--arch", "not a Docker run option", "select guest architecture", "keep as Apple extension", "D1"),
    OptionSemantics("run", "--no-dns", "not a Docker run option", "disable DNS configuration", "keep as Apple extension", "D1"),
    OptionSemantics("run", "--env-file", "KEY inherits host value; # comments", "key=value file; semantics unverified", "spike before claiming equivalence", "D1; C2"),
    OptionSemantics("kill", "-s", "signal name, SIG-prefixed name, or number", "signal spelling unverified", "normalize accepted spellings", "D1; C3"),
    OptionSemantics("stop", "-s", "stop signal name or number", "signal spelling unverified", "normalize accepted spellings", "D1; C3"),
    OptionSemantics("run", "--pids-limit", "limit process count", "no equivalent", "refuse unless existing waiver applies", "D1"),
    OptionSemantics("run", "--security-opt", "configure security profile", "no equivalent", "refuse unless existing waiver applies", "D1"),
    OptionSemantics("run", "--privileged", "disable isolation restrictions", "no equivalent", "refuse unless existing waiver applies", "D1"),
    OptionSemantics("run", "--device", "pass host device", "no equivalent", "refuse", "D1"),
    OptionSemantics("run", "--ipc", "select IPC namespace", "no equivalent", "refuse", "D1"),
    OptionSemantics("run", "--pid", "select PID namespace", "no equivalent", "refuse", "D1"),
    OptionSemantics("run", "--group-add", "add supplementary groups", "no equivalent", "refuse", "D1"),
    OptionSemantics("build", "--rm", "remove intermediate containers", "no equivalent", "accept as no-op", "D1"),
    OptionSemantics("build", "--force-rm", "force remove intermediate containers", "no equivalent", "accept as no-op", "D1"),
    OptionSemantics("build", "--memory", "builder memory limit", "builder memory limit", "keep; normalize units", "D1"),
    OptionSemantics("build", "--cpus", "Docker build has no --cpus option", "builder CPU limit extension", "keep as Apple extension", "D1"),
)
