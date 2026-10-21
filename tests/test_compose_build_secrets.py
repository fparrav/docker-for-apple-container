from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from container_docker_shim import compose_build_secrets  # noqa: E402
from container_docker_shim.compose_build_secrets import BuildSecret  # noqa: E402

TIER = "unit"

SENTINEL = "SENTINEL-SECRET-VALUE-MUST-NEVER-LEAK"


class FakeService:
    """Duck-typed stand-in for a compose service: just a name."""

    def __init__(self, name: str) -> None:
        self.name = name


def _all_strings(secrets, problems, notices, args, check_problems):
    return [
        *problems,
        *notices,
        *args,
        *check_problems,
        *[f"{s.id} {s.kind} {s.value}" for s in secrets],
    ]


class ResolveEntryFormsTests(unittest.TestCase):
    def test_string_entry_resolves_to_file_secret(self):
        service = FakeService("api")
        secrets, problems, notices = compose_build_secrets.resolve(
            service, ["npm_token"], {"npm_token": {"file": "./npm-token.txt"}}, "/proj"
        )
        self.assertEqual(problems, [])
        self.assertEqual(notices, [])
        self.assertEqual(
            secrets, [BuildSecret(id="npm_token", kind="file", value="/proj/npm-token.txt")]
        )

    def test_mapping_entry_resolves_like_the_string_form(self):
        service = FakeService("api")
        secrets, problems, notices = compose_build_secrets.resolve(
            service, [{"source": "npm_token"}], {"npm_token": {"file": "./npm-token.txt"}},
            "/proj",
        )
        self.assertEqual(problems, [])
        self.assertEqual(
            secrets, [BuildSecret(id="npm_token", kind="file", value="/proj/npm-token.txt")]
        )

    def test_target_renames_the_id(self):
        service = FakeService("api")
        secrets, problems, notices = compose_build_secrets.resolve(
            service, [{"source": "npm_token", "target": "registry"}],
            {"npm_token": {"file": "/abs/token.txt"}}, "/proj",
        )
        self.assertEqual(problems, [])
        self.assertEqual(notices, [])
        self.assertEqual(
            secrets, [BuildSecret(id="registry", kind="file", value="/abs/token.txt")]
        )


class ResolveFilePathTests(unittest.TestCase):
    def test_relative_path_resolves_against_project_dir(self):
        service = FakeService("api")
        secrets, _, _ = compose_build_secrets.resolve(
            service, ["t"], {"t": {"file": "secrets/token.txt"}}, "/proj/app"
        )
        self.assertEqual(secrets[0].value, "/proj/app/secrets/token.txt")

    def test_absolute_path_is_kept(self):
        service = FakeService("api")
        secrets, _, _ = compose_build_secrets.resolve(
            service, ["t"], {"t": {"file": "/etc/token.txt"}}, "/proj/app"
        )
        self.assertEqual(secrets[0].value, "/etc/token.txt")

    def test_dollar_in_path_stays_literal(self):
        service = FakeService("api")
        secrets, _, _ = compose_build_secrets.resolve(
            service, ["t"], {"t": {"file": "./with$dollar/token.txt"}}, "/proj"
        )
        self.assertEqual(secrets[0].value, "/proj/with$dollar/token.txt")


class ResolveEnvironmentTests(unittest.TestCase):
    def test_environment_kind_uses_the_variable_name(self):
        service = FakeService("api")
        secrets, problems, notices = compose_build_secrets.resolve(
            service, ["npm_token"], {"npm_token": {"environment": "NPM_TOKEN"}}, "/proj"
        )
        self.assertEqual(problems, [])
        self.assertEqual(notices, [])
        self.assertEqual(
            secrets, [BuildSecret(id="npm_token", kind="env", value="NPM_TOKEN")]
        )


class ResolveProblemsTests(unittest.TestCase):
    def test_undefined_name_is_a_problem(self):
        service = FakeService("api")
        secrets, problems, notices = compose_build_secrets.resolve(
            service, ["missing"], {}, "/proj"
        )
        self.assertEqual(secrets, [])
        self.assertEqual(notices, [])
        self.assertEqual(len(problems), 1)
        self.assertIn("api", problems[0])
        self.assertIn("missing", problems[0])

    def test_external_is_a_problem(self):
        service = FakeService("api")
        secrets, problems, _ = compose_build_secrets.resolve(
            service, ["t"], {"t": {"external": True}}, "/proj"
        )
        self.assertEqual(secrets, [])
        self.assertTrue(any("external" in p for p in problems))

    def test_both_file_and_environment_is_a_problem(self):
        service = FakeService("api")
        secrets, problems, _ = compose_build_secrets.resolve(
            service, ["t"], {"t": {"file": "/a", "environment": "B"}}, "/proj"
        )
        self.assertEqual(secrets, [])
        self.assertTrue(any("both" in p for p in problems))

    def test_neither_file_nor_environment_is_a_problem(self):
        service = FakeService("api")
        secrets, problems, _ = compose_build_secrets.resolve(
            service, ["t"], {"t": {"name": "t"}}, "/proj"
        )
        self.assertEqual(secrets, [])
        self.assertTrue(any("needs a" in p for p in problems))

    def test_unknown_entry_attribute_is_a_problem(self):
        service = FakeService("api")
        secrets, problems, _ = compose_build_secrets.resolve(
            service, [{"source": "t", "bogus": 1}], {"t": {"file": "/a"}}, "/proj"
        )
        self.assertEqual(secrets, [])
        self.assertTrue(any("bogus" in p for p in problems))

    def test_unknown_definition_attribute_is_a_problem(self):
        service = FakeService("api")
        secrets, problems, _ = compose_build_secrets.resolve(
            service, ["t"], {"t": {"file": "/a", "bogus": 1}}, "/proj"
        )
        self.assertEqual(secrets, [])
        self.assertTrue(any("bogus" in p for p in problems))

    def test_problems_name_the_service_and_id(self):
        service = FakeService("api")
        _, problems, _ = compose_build_secrets.resolve(
            service, [{"source": "t", "target": "renamed"}],
            {"t": {"external": True}}, "/proj",
        )
        self.assertTrue(problems)
        self.assertTrue(all("api" in p for p in problems))
        self.assertTrue(all("renamed" in p for p in problems))


class ResolveNoticesTests(unittest.TestCase):
    def test_uid_gid_mode_on_entry_give_notices_not_problems(self):
        service = FakeService("api")
        secrets, problems, notices = compose_build_secrets.resolve(
            service, [{"source": "t", "uid": "1000", "gid": "1000", "mode": "0400"}],
            {"t": {"file": "/a"}}, "/proj",
        )
        self.assertEqual(problems, [])
        self.assertEqual(secrets, [BuildSecret(id="t", kind="file", value="/a")])
        self.assertEqual(len(notices), 3)
        self.assertTrue(all(n.startswith("NOT APPLIED:") for n in notices))
        self.assertTrue(any("uid" in n for n in notices))
        self.assertTrue(any("gid" in n for n in notices))
        self.assertTrue(any("mode" in n for n in notices))

    def test_uid_gid_mode_on_definition_give_notices(self):
        service = FakeService("api")
        secrets, problems, notices = compose_build_secrets.resolve(
            service, ["t"], {"t": {"file": "/a", "uid": "1000", "mode": "0400"}}, "/proj"
        )
        self.assertEqual(problems, [])
        self.assertEqual(secrets, [BuildSecret(id="t", kind="file", value="/a")])
        self.assertEqual(len(notices), 2)
        self.assertTrue(any("uid" in n for n in notices))
        self.assertTrue(any("mode" in n for n in notices))


class CheckTests(unittest.TestCase):
    def test_missing_file_is_a_problem(self):
        secrets = [BuildSecret(id="t", kind="file", value="/nope/token.txt")]
        problems = compose_build_secrets.check(
            secrets, isfile=lambda p: p == "/exists", environ={}, service="api"
        )
        self.assertEqual(len(problems), 1)
        self.assertIn("api", problems[0])
        self.assertIn("t", problems[0])
        self.assertIn("/nope/token.txt", problems[0])

    def test_present_file_is_not_a_problem(self):
        secrets = [BuildSecret(id="t", kind="file", value="/exists")]
        problems = compose_build_secrets.check(
            secrets, isfile=lambda p: p == "/exists", environ={}, service="api"
        )
        self.assertEqual(problems, [])

    def test_unset_variable_is_a_problem(self):
        secrets = [BuildSecret(id="t", kind="env", value="NPM_TOKEN")]
        problems = compose_build_secrets.check(
            secrets, isfile=lambda p: True, environ={}, service="api"
        )
        self.assertEqual(len(problems), 1)
        self.assertIn("api", problems[0])
        self.assertIn("NPM_TOKEN", problems[0])

    def test_set_but_empty_variable_is_accepted(self):
        secrets = [BuildSecret(id="t", kind="env", value="NPM_TOKEN")]
        problems = compose_build_secrets.check(
            secrets, isfile=lambda p: True, environ={"NPM_TOKEN": ""}, service="api"
        )
        self.assertEqual(problems, [])

    def test_set_variable_is_not_a_problem(self):
        secrets = [BuildSecret(id="t", kind="env", value="NPM_TOKEN")]
        problems = compose_build_secrets.check(
            secrets, isfile=lambda p: True, environ={"NPM_TOKEN": "somevalue"}, service="api"
        )
        self.assertEqual(problems, [])


class ToArgsTests(unittest.TestCase):
    def test_file_and_env_argument_shape(self):
        secrets = [
            BuildSecret(id="npm_token", kind="file", value="/abs/token.txt"),
            BuildSecret(id="registry", kind="env", value="NPM_TOKEN"),
        ]
        self.assertEqual(
            compose_build_secrets.to_args(secrets),
            [
                "--secret", "id=npm_token,src=/abs/token.txt",
                "--secret", "id=registry,env=NPM_TOKEN",
            ],
        )

    def test_empty_secrets_give_no_args(self):
        self.assertEqual(compose_build_secrets.to_args([]), [])


class SentinelLeakTests(unittest.TestCase):
    def test_secret_value_never_appears_in_any_returned_string(self):
        service = FakeService("api")
        entries = [{"source": "npm_token", "target": "registry"}]
        definitions = {"npm_token": {"environment": "NPM_TOKEN"}}
        secrets, problems, notices = compose_build_secrets.resolve(
            service, entries, definitions, "/proj"
        )
        args = compose_build_secrets.to_args(secrets)
        check_problems = compose_build_secrets.check(
            secrets, isfile=lambda p: True, environ={"NPM_TOKEN": SENTINEL}, service="api"
        )
        strings = _all_strings(secrets, problems, notices, args, check_problems)
        self.assertTrue(strings)  # sanity: output was actually produced
        for s in strings:
            self.assertNotIn(SENTINEL, s)


if __name__ == "__main__":
    unittest.main()
