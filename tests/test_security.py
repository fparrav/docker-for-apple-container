from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from container_docker_shim import security  # noqa: E402

TIER = "unit"


class SecurityRulesTests(unittest.TestCase):
    def test_unsupported_rules_have_a_reason(self):
        for rule in security.RULES.values():
            if rule.status == security.UNSUPPORTED:
                self.assertTrue(rule.reason, rule.name)

    def test_supported_rules_have_an_apple_flag(self):
        for rule in security.RULES.values():
            if rule.status == security.SUPPORTED:
                self.assertTrue(
                    rule.apple_flag and rule.apple_flag.startswith("--"),
                    rule.name,
                )

    def test_lookup_accepts_option_with_or_without_dashes(self):
        with_dashes = security.lookup("--security-opt")
        without_dashes = security.lookup("security-opt")
        self.assertIs(with_dashes, without_dashes)
        self.assertEqual(with_dashes.status, security.UNSUPPORTED)

    def test_lookup_returns_none_for_unknown_option(self):
        self.assertIsNone(security.lookup("no-such-option"))

    def test_known_unsupported_options_are_present(self):
        names = (
            "security-opt",
            "pids-limit",
            "storage-opt",
            "privileged",
            "sysctls",
            "devices",
        )
        for name in names:
            rule = security.RULES.get(name)
            self.assertIsNotNone(rule, name)
            self.assertEqual(rule.status, security.UNSUPPORTED, name)


class UnenforcedOptInTests(unittest.TestCase):
    def test_missing_variable_allows_nothing(self):
        self.assertEqual(security.unenforced_allowed({}), frozenset())

    def test_single_name(self):
        env = {security.ENV_ALLOW: "security-opt"}
        self.assertEqual(security.unenforced_allowed(env), frozenset({"security-opt"}))

    def test_multiple_names_with_whitespace_and_trailing_comma(self):
        env = {security.ENV_ALLOW: "security-opt , pids-limit,"}
        self.assertEqual(
            security.unenforced_allowed(env),
            frozenset({"security-opt", "pids-limit"}),
        )

    def test_unknown_name_is_returned_as_is(self):
        env = {security.ENV_ALLOW: "foo"}
        self.assertEqual(security.unenforced_allowed(env), frozenset({"foo"}))

    def test_wildcard_is_just_another_name_and_refuses_anyway(self):
        env = {security.ENV_ALLOW: "*"}
        self.assertEqual(security.unenforced_allowed(env), frozenset({"*"}))
        self.assertEqual(security.decide("security-opt", env).action, "refuse")

    def test_supported_option_is_ok(self):
        decision = security.decide("cap-add", {})
        self.assertEqual(decision.action, "ok")
        self.assertEqual(decision.message, "")

    def test_unsupported_option_without_opt_in_refuses(self):
        decision = security.decide("security-opt", {})
        self.assertEqual(decision.action, "refuse")
        self.assertIn("--security-opt", decision.message)
        self.assertIn(security.RULES["security-opt"].reason, decision.message)
        self.assertIn("CONTAINER_DOCKER_SHIM_ALLOW_UNENFORCED", decision.message)

    def test_unsupported_option_with_opt_in_skips_with_warning(self):
        env = {security.ENV_ALLOW: "security-opt"}
        decision = security.decide("security-opt", env)
        self.assertEqual(decision.action, "skip")
        self.assertTrue(decision.message.startswith("warning:"))

    def test_opt_in_only_covers_the_named_option(self):
        env = {security.ENV_ALLOW: "security-opt"}
        self.assertEqual(security.decide("pids-limit", env).action, "refuse")

    def test_leading_dashes_are_accepted(self):
        self.assertEqual(security.decide("--privileged", {}).action, "refuse")

    def test_unknown_option_is_ok(self):
        self.assertEqual(security.decide("no-such-option", {}).action, "ok")

    def test_waivable_rules_are_only_the_restriction_options(self):
        self.assertEqual(
            {
                name
                for name, rule in security.RULES.items()
                if rule.status == security.UNSUPPORTED and rule.waivable
            },
            {
                "security-opt",
                "pids-limit",
                "storage-opt",
                "privileged",
                "sysctls",
                "devices",
            },
        )

    def test_waiving_network_none_still_refuses(self):
        env = {security.ENV_ALLOW: "network-none"}
        decision = security.decide("network-none", env)
        self.assertEqual(decision.action, "refuse")
        self.assertIn("cannot be waived", decision.message)
        self.assertIn(security.ENV_ALLOW, decision.message)

    def test_network_none_without_opt_in_refuses(self):
        decision = security.decide("network-none", {})
        self.assertEqual(decision.action, "refuse")
        self.assertIn("cannot be waived", decision.message)

    def test_waiving_pids_limit_skips(self):
        env = {security.ENV_ALLOW: "pids-limit"}
        decision = security.decide("pids-limit", env)
        self.assertEqual(decision.action, "skip")

    def test_hostname_and_add_host_cannot_be_waived(self):
        for name in ("hostname", "add-host"):
            with self.subTest(name=name):
                self.assertFalse(security.RULES[name].waivable, name)
                self.assertEqual(
                    security.decide(name, {security.ENV_ALLOW: name}).action,
                    "refuse",
                )

    def test_unknown_waivers_reports_a_typo(self):
        env = {security.ENV_ALLOW: "pid-limit"}
        self.assertEqual(security.unknown_waivers(env), ["pid-limit"])

    def test_unknown_waivers_names_non_waivable_options(self):
        env = {security.ENV_ALLOW: "network-none"}
        self.assertEqual(security.unknown_waivers(env), ["network-none"])

    def test_unknown_waivers_is_sorted_and_without_duplicates(self):
        env = {security.ENV_ALLOW: "zzz, pids-limit, zzz, network-none"}
        self.assertEqual(security.unknown_waivers(env), ["network-none", "zzz"])

    def test_unknown_waivers_empty_for_valid_names(self):
        env = {security.ENV_ALLOW: "security-opt, pids-limit"}
        self.assertEqual(security.unknown_waivers(env), [])

    def test_unknown_waivers_empty_without_variable(self):
        self.assertEqual(security.unknown_waivers({}), [])


class ReadmeTableTests(unittest.TestCase):
    def setUp(self):
        text = (ROOT / "README.md").read_text(encoding="utf-8")
        section = text.split("### Security options", 1)[1].split("### ", 1)[0]
        self.rows = re.findall(
            r"^\| `([^`]+)` \| (\w+) \| (.*) \|$", section, re.MULTILINE
        )

    def test_every_rule_is_in_the_readme_table_with_the_same_status(self):
        table = dict((name, (status, reason)) for name, status, reason in self.rows)
        for rule in security.RULES.values():
            with self.subTest(rule=rule.name):
                self.assertIn(rule.name, table)
                if rule.name in table:
                    status, reason = table[rule.name]
                    self.assertEqual(status, rule.status)
                    self.assertEqual(reason, rule.reason)

    def test_readme_table_has_no_unknown_rows(self):
        self.assertEqual({name for name, _, _ in self.rows}, set(security.RULES))

    def test_each_option_appears_once(self):
        self.assertEqual(len(self.rows), len(security.RULES))


if __name__ == "__main__":
    unittest.main()
