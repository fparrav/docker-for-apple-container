"""Contract test CT3 (design D8): build secrets resolve to arguments or a problem.

Three contracts, all on the pure resolver in ``compose_build_secrets``:

1. A table of secret definitions and ``build.secrets`` entries (string
   entry, ``{source, target}`` mapping, file, environment, both, neither,
   external, undefined name, unknown attribute, uid/gid/mode notices) must
   resolve, for every non-empty entry list, to either non-empty secrets --
   whose ``to_args`` output matches the ``--secret`` shape -- or a
   non-empty problems list, never both empty.
2. The official-shaped model (``convert_model`` of the recorded
   ``docker-compose config --format json`` fixture, with ``__PROJECT_DIR__``
   replaced as in ``test_contract_normalizer_loss``) and the built-in
   parser's model of the equivalent compose file must resolve to identical
   ``BuildSecret`` lists: the fixture carries the binary's absolute file
   paths, the compose file the relative ones, and the same project
   directory must reconcile them.
3. A mutation that makes the resolver silently drop entries is detected by
   the same helper that implements (1).

No secret value may appear in any asserted message: a sentinel value in the
environment and in a secret file must never reach a problem, notice or
argument.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from container_docker_shim import compose  # noqa: E402
from container_docker_shim import compose_build_secrets  # noqa: E402
from container_docker_shim import compose_normalizer  # noqa: E402
from container_docker_shim.compose_build_secrets import BuildSecret  # noqa: E402

TIER = "unit"

VERSION = "5.6.0"
PROJECT_DIR = "/project"
FIXTURE = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "compose"
    / "normalized"
    / "build-secrets.json"
)
SENTINEL = "SENTINEL-SECRET-VALUE-MUST-NEVER-LEAK"


# --------------------------------------------------------------------------
# (1) The resolver table
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Case:
    """One row of the resolver table.

    ``expect_secrets`` is the exact ``BuildSecret`` list the case must
    resolve to (empty when the entry must be refused); ``expect_problems``
    and ``expect_notices`` are exact counts, or ``-1`` for "at least one".
    """

    name: str
    entries: object
    definitions: dict
    expect_secrets: tuple
    expect_problems: int
    expect_notices: int


CASES = (
    Case(
        "string entry, file definition (relative path)",
        ["npm_token"],
        {"npm_token": {"file": "npm-token.txt"}},
        (BuildSecret(id="npm_token", kind="file", value="/project/npm-token.txt"),),
        0,
        0,
    ),
    Case(
        "mapping entry without target, file definition",
        [{"source": "npm_token"}],
        {"npm_token": {"file": "npm-token.txt"}},
        (BuildSecret(id="npm_token", kind="file", value="/project/npm-token.txt"),),
        0,
        0,
    ),
    Case(
        "mapping entry with target renames the id",
        [{"source": "api_key", "target": "registry"}],
        {"api_key": {"environment": "API_KEY"}},
        (BuildSecret(id="registry", kind="env", value="API_KEY"),),
        0,
        0,
    ),
    Case(
        "string entry, environment definition",
        ["api_key"],
        {"api_key": {"environment": "API_KEY"}},
        (BuildSecret(id="api_key", kind="env", value="API_KEY"),),
        0,
        0,
    ),
    Case(
        "absolute file path is kept as is",
        ["token"],
        {"token": {"file": "/abs/token.txt"}},
        (BuildSecret(id="token", kind="file", value="/abs/token.txt"),),
        0,
        0,
    ),
    Case(
        "definition with both file and environment is refused",
        ["t"],
        {"t": {"file": "/a", "environment": "B"}},
        (),
        1,
        0,
    ),
    Case(
        "definition with neither file nor environment is refused",
        ["t"],
        {"t": {"name": "t"}},
        (),
        1,
        0,
    ),
    Case(
        "external true is refused",
        ["t"],
        {"t": {"external": True, "file": "/a"}},
        (),
        1,
        0,
    ),
    Case(
        "legacy external object form is refused",
        ["t"],
        {"t": {"external": {"name": "x"}, "file": "/a"}},
        (),
        1,
        0,
    ),
    Case(
        "undefined secret name is refused",
        ["missing"],
        {},
        (),
        1,
        0,
    ),
    Case(
        "entry without a source is refused",
        [{"target": "x"}],
        {},
        (),
        1,
        0,
    ),
    Case(
        "unknown entry attribute is refused",
        [{"source": "t", "bogus": 1}],
        {"t": {"file": "/a"}},
        (),
        1,
        0,
    ),
    Case(
        "unknown definition attribute is refused",
        ["t"],
        {"t": {"file": "/a", "bogus": 1}},
        (),
        1,
        0,
    ),
    Case(
        "uid/gid/mode on the entry are notices, not refusals",
        [{"source": "t", "uid": "1000", "gid": "1000", "mode": "0400"}],
        {"t": {"file": "/a"}},
        (BuildSecret(id="t", kind="file", value="/a"),),
        0,
        -1,
    ),
    Case(
        "uid/mode on the definition are notices, not refusals",
        ["t"],
        {"t": {"file": "/a", "uid": "1000", "mode": "0400"}},
        (BuildSecret(id="t", kind="file", value="/a"),),
        0,
        -1,
    ),
    Case(
        "a valid entry and an external one resolve together",
        ["npm_token", "ext"],
        {"npm_token": {"file": "npm-token.txt"}, "ext": {"external": True}},
        (BuildSecret(id="npm_token", kind="file", value="/project/npm-token.txt"),),
        -1,
        0,
    ),
)


def _expected_args(secrets) -> list:
    """The ``--secret`` arguments the BuildSecrets demand, from their fields.

    A file secret demands ``--secret id=<id>,src=<path>``; an environment
    secret demands ``--secret id=<id>,env=<VAR>``. Any other kind yields a
    marker that can never match ``to_args`` output.
    """
    args: list = []
    for secret in secrets:
        if secret.kind == "file":
            args.extend(["--secret", f"id={secret.id},src={secret.value}"])
        elif secret.kind == "env":
            args.extend(["--secret", f"id={secret.id},env={secret.value}"])
        else:
            args.append(f"unexpected kind {secret.kind!r}")
    return args


def _violations() -> list:
    """Table cases that break the CT3 contract (empty = the contract holds).

    A case is an offender when its non-empty ``build.secrets`` entries
    resolve to neither secrets nor problems, or when ``to_args`` does not
    emit the ``--secret`` arguments the resolved secrets demand.
    """
    offenders: list = []
    for case in CASES:
        service = compose.Service("api", {})
        secrets, problems, _notices = compose_build_secrets.resolve(
            service, case.entries, case.definitions, PROJECT_DIR
        )
        if case.entries and not secrets and not problems:
            offenders.append(
                f"{case.name}: non-empty build.secrets resolved to neither "
                "secrets nor problems"
            )
        if compose_build_secrets.to_args(secrets) != _expected_args(secrets):
            offenders.append(
                f"{case.name}: to_args does not match the --secret shape"
            )
    return offenders


class ResolverTableTests(unittest.TestCase):
    """(1) every table case resolves to arguments or a problem, never neither."""

    def test_cases_resolve_to_their_expected_secrets_and_problems(self):
        for case in CASES:
            with self.subTest(case=case.name):
                service = compose.Service("api", {})
                secrets, problems, notices = compose_build_secrets.resolve(
                    service, case.entries, case.definitions, PROJECT_DIR
                )
                self.assertEqual(list(secrets), list(case.expect_secrets))
                self._count(problems, case.expect_problems, "problems")
                self._count(notices, case.expect_notices, "notices")

    def test_never_both_empty_and_args_match_the_secrets(self):
        self.assertEqual(_violations(), [])

    def _count(self, actual: list, expected: int, label: str) -> None:
        if expected >= 0:
            self.assertEqual(len(actual), expected, label)
        else:
            self.assertTrue(actual, f"{label}: expected at least one")


# --------------------------------------------------------------------------
# (2) Official-shaped model vs built-in parser model
# --------------------------------------------------------------------------


# The compose file the recorded fixture was normalized from: the same
# project with the spellings the official binary rewrites (relative file
# paths, short volume syntax, string secret entry) that the built-in parser
# reads as written.
EQUIVALENT_COMPOSE = """\
name: bsec
services:
  api:
    image: alpine:3.20
    build:
      context: .
      dockerfile: Dockerfile
      secrets:
        - npm_token
        - source: api_key
          target: registry
    secrets:
      - source: runtime_only
        target: /run/secrets/runtime_only
    volumes:
      - data:/data
secrets:
  api_key:
    environment: API_KEY
  npm_token:
    file: npm-token.txt
  runtime_only:
    file: runtime.txt
volumes:
  data:
    labels:
      cost: $5
"""


def _official_model() -> dict:
    text = FIXTURE.read_text(encoding="utf-8").replace("__PROJECT_DIR__", PROJECT_DIR)
    return compose_normalizer.convert_model(json.loads(text), VERSION)


def _builtin_model() -> dict:
    """The built-in parser's model of the equivalent compose file.

    The file is written to a temporary directory and read back; the
    parser's entry is ``compose.parse_yaml`` on its text.
    """
    with tempfile.TemporaryDirectory() as tmp:
        compose_file = Path(tmp) / "compose.yaml"
        compose_file.write_text(EQUIVALENT_COMPOSE, encoding="utf-8")
        return compose.parse_yaml(compose_file.read_text(encoding="utf-8"))


def _resolved_secrets(project: compose.Project) -> list:
    """The BuildSecrets ``compose_build_secrets.resolve`` yields for ``api``."""
    service = project.services["api"]
    secrets, _problems, _notices = compose_build_secrets.resolve(
        service,
        service.spec.get("build", {}).get("secrets"),
        project.secrets,
        project.directory,
    )
    return secrets


class ModelEquivalenceTests(unittest.TestCase):
    """(2) the official-shaped model and the built-in parser's model agree."""

    def test_both_models_resolve_to_identical_build_secrets(self):
        # Both projects carry the same directory: the fixture's
        # __PROJECT_DIR__ became /project, and the built-in model's relative
        # paths resolve against the same directory, so the two models must
        # reconcile into identical BuildSecret lists.
        official = compose.Project("bsec", _official_model(), PROJECT_DIR)
        builtin = compose.Project("bsec", _builtin_model(), PROJECT_DIR)
        official_secrets = _resolved_secrets(official)
        builtin_secrets = _resolved_secrets(builtin)
        self.assertTrue(official_secrets)
        self.assertEqual(official_secrets, builtin_secrets)
        self.assertEqual(
            official_secrets,
            [
                BuildSecret(id="npm_token", kind="file", value="/project/npm-token.txt"),
                BuildSecret(id="registry", kind="env", value="API_KEY"),
            ],
        )


# --------------------------------------------------------------------------
# (3) Mutation: the invariant helper must fail when entries are dropped
# --------------------------------------------------------------------------


class MutationTests(unittest.TestCase):
    """(3) a resolver that silently drops entries is detected by the helper."""

    def test_a_resolver_that_silently_drops_entries_is_detected(self):
        with mock.patch.object(
            compose_build_secrets,
            "_resolve_entry",
            lambda service_name, entry, definitions, project_dir: ([], [], []),
        ):
            offenders = _violations()
        self.assertTrue(offenders)
        self.assertEqual(len(offenders), len(CASES))


# --------------------------------------------------------------------------
# No secret value in any message
# --------------------------------------------------------------------------


class NoValueLeakTests(unittest.TestCase):
    """No secret value may appear in any message the resolver produces."""

    def test_environment_value_never_reaches_any_message(self):
        service = compose.Service("api", {})
        secrets, problems, notices = compose_build_secrets.resolve(
            service,
            [{"source": "api_key", "target": "registry"}],
            {"api_key": {"environment": "API_KEY"}},
            PROJECT_DIR,
        )
        args = compose_build_secrets.to_args(secrets)
        check_problems = compose_build_secrets.check(
            secrets,
            isfile=lambda path: False,
            environ={"API_KEY": SENTINEL},
            service="api",
        )
        strings = [
            *problems,
            *notices,
            *args,
            *check_problems,
            *[f"{s.id} {s.kind} {s.value}" for s in secrets],
        ]
        self.assertTrue(strings)
        for string in strings:
            self.assertNotIn(SENTINEL, string)

    def test_file_content_never_reaches_any_message(self):
        with tempfile.TemporaryDirectory() as tmp:
            token = Path(tmp) / "token.txt"
            token.write_text(SENTINEL, encoding="utf-8")
            secrets = [BuildSecret(id="t", kind="file", value=str(token))]
            problems = compose_build_secrets.check(
                secrets, isfile=lambda path: False, environ={}, service="api"
            )
            self.assertEqual(len(problems), 1)
            self.assertIn(str(token), problems[0])
            self.assertNotIn(SENTINEL, problems[0])


if __name__ == "__main__":
    unittest.main()
