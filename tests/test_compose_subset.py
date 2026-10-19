from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from container_docker_shim import compose_subset  # noqa: E402


class AnchorTests(unittest.TestCase):
    def test_anchor_on_map_key(self):
        with self.assertRaises(compose_subset.SubsetError) as caught:
            compose_subset.check_subset("name: &app foo\n")
        self.assertEqual(caught.exception.construct, "anchor")
        self.assertEqual(caught.exception.line, 1)

    def test_anchor_on_sequence_item(self):
        text = "command:\n  - &args sh -c \"echo hi\"\n"
        with self.assertRaises(compose_subset.SubsetError) as caught:
            compose_subset.check_subset(text)
        self.assertEqual(caught.exception.construct, "anchor")
        self.assertEqual(caught.exception.line, 2)

    def test_message_names_line_construct_and_fix(self):
        with self.assertRaises(compose_subset.SubsetError) as caught:
            compose_subset.check_subset("a:\nb: &app foo\n")
        self.assertEqual(caught.exception.line, 2)
        self.assertEqual(
            str(caught.exception),
            "line 2: anchor is not supported by the built-in compose "
            "parser; install docker-compose (brew install docker-compose)",
        )


class AliasTests(unittest.TestCase):
    def test_alias_value(self):
        with self.assertRaises(compose_subset.SubsetError) as caught:
            compose_subset.check_subset("command: *args\n")
        self.assertEqual(caught.exception.construct, "alias")
        self.assertEqual(caught.exception.line, 1)

    def test_alias_sequence_item(self):
        text = "environment:\n  - A=1\ncommand:\n  - *args\n"
        with self.assertRaises(compose_subset.SubsetError) as caught:
            compose_subset.check_subset(text)
        self.assertEqual(caught.exception.construct, "alias")
        self.assertEqual(caught.exception.line, 4)


class MergeKeyTests(unittest.TestCase):
    def test_merge_key(self):
        text = "services:\n  app:\n    <<: *base\n"
        with self.assertRaises(compose_subset.SubsetError) as caught:
            compose_subset.check_subset(text)
        self.assertEqual(caught.exception.construct, "merge key")
        self.assertEqual(caught.exception.line, 3)

    def test_merge_key_as_sequence_item(self):
        text = "x:\n  - <<: *base\n"
        with self.assertRaises(compose_subset.SubsetError) as caught:
            compose_subset.check_subset(text)
        self.assertEqual(caught.exception.construct, "merge key")
        self.assertEqual(caught.exception.line, 2)


class BlockScalarTests(unittest.TestCase):
    def test_block_scalar_map_values(self):
        for token in ("|", ">", "|-", ">+", "|2"):
            with self.assertRaises(
                compose_subset.SubsetError, msg=token
            ) as caught:
                compose_subset.check_subset("command: %s\n" % token)
            self.assertEqual(caught.exception.construct, "block scalar")
            self.assertEqual(caught.exception.line, 1)

    def test_block_scalar_sequence_items(self):
        for token in ("|", ">", "|-", ">+", "|2"):
            with self.assertRaises(
                compose_subset.SubsetError, msg=token
            ) as caught:
                compose_subset.check_subset("command:\n  - %s\n" % token)
            self.assertEqual(caught.exception.construct, "block scalar")
            self.assertEqual(caught.exception.line, 2)

    def test_block_scalar_with_trailing_comment(self):
        text = "command: | # multiline\n"
        with self.assertRaises(compose_subset.SubsetError) as caught:
            compose_subset.check_subset(text)
        self.assertEqual(caught.exception.construct, "block scalar")
        self.assertEqual(caught.exception.line, 1)

    def test_message_names_line_construct_and_fix(self):
        with self.assertRaises(compose_subset.SubsetError) as caught:
            compose_subset.check_subset("a:\nb: |-\n")
        self.assertEqual(caught.exception.line, 2)
        self.assertEqual(
            str(caught.exception),
            "line 2: block scalar is not supported by the built-in "
            "compose parser; install docker-compose "
            "(brew install docker-compose)",
        )


class NoFalsePositiveTests(unittest.TestCase):
    def test_quoted_anchor_and_alias_values(self):
        self.assertIsNone(
            compose_subset.check_subset('a: "&x"\nb: \'*x\'\n')
        )

    def test_ampersands_inside_quoted_shell_command(self):
        self.assertIsNone(
            compose_subset.check_subset('command: sh -c "a && b"\n')
        )

    def test_asterisk_inside_quoted_list_item(self):
        self.assertIsNone(
            compose_subset.check_subset('command: ["sh", "-c", "echo *"]\n')
        )

    def test_url_with_ampersand(self):
        self.assertIsNone(
            compose_subset.check_subset("image: http://x?a=1&b=2\n")
        )

    def test_pipe_inside_plain_value(self):
        self.assertIsNone(compose_subset.check_subset("command: a | b\n"))

    def test_pipe_inside_quoted_value(self):
        self.assertIsNone(
            compose_subset.check_subset('command: "a | b"\n')
        )

    def test_comment_with_anchor(self):
        self.assertIsNone(compose_subset.check_subset("# &anchor\n"))

    def test_ampersand_in_environment_value(self):
        self.assertIsNone(compose_subset.check_subset("environment: A&B\n"))

    def test_bare_plain_lines_and_blocks(self):
        text = (
            "services:\n"
            "  app:\n"
            "    image: nginx\n"
            "    ports:\n"
            "      - \"8080:80\"\n"
            "    environment: FOO=bar\n"
            "    command: [\"nginx\", \"-g\", \"daemon off;\"]\n"
        )
        self.assertIsNone(compose_subset.check_subset(text))


class DocumentTests(unittest.TestCase):
    def test_leading_document_start_is_allowed(self):
        text = "---\nservices:\n  app:\n    image: nginx\n"
        self.assertIsNone(compose_subset.check_subset(text))

    def test_leading_document_start_after_comment_is_allowed(self):
        text = "# compose file\n---\na: 1\n"
        self.assertIsNone(compose_subset.check_subset(text))

    def test_document_start_after_content_is_rejected(self):
        text = "services:\n  app:\n---\nimage: nginx\n"
        with self.assertRaises(compose_subset.SubsetError) as caught:
            compose_subset.check_subset(text)
        self.assertEqual(caught.exception.construct, "multi-document stream")
        self.assertEqual(caught.exception.line, 3)

    def test_second_leading_document_start_is_rejected(self):
        text = "---\n---\na: 1\n"
        with self.assertRaises(compose_subset.SubsetError) as caught:
            compose_subset.check_subset(text)
        self.assertEqual(caught.exception.construct, "multi-document stream")
        self.assertEqual(caught.exception.line, 2)

    def test_document_end_is_rejected(self):
        text = "a: 1\n...\nb: 2\n"
        with self.assertRaises(compose_subset.SubsetError) as caught:
            compose_subset.check_subset(text)
        self.assertEqual(caught.exception.construct, "multi-document stream")
        self.assertEqual(caught.exception.line, 2)

    def test_document_end_on_first_line_is_rejected(self):
        with self.assertRaises(compose_subset.SubsetError) as caught:
            compose_subset.check_subset("...\na: 1\n")
        self.assertEqual(caught.exception.construct, "multi-document stream")
        self.assertEqual(caught.exception.line, 1)

    def test_indented_ellipsis_is_data(self):
        text = "a: ...\nb:\n  - ...\n"
        self.assertIsNone(compose_subset.check_subset(text))

    def test_content_on_the_document_start_line_is_still_checked(self):
        cases = (
            ("--- &a\nservices: {}\n", "anchor"),
            ("--- *a\n", "alias"),
            ("--- include: file.yaml\n", "include"),
            ("--- <<: {a: 1}\n", "merge key"),
            ("--- |\n  text\n", "block scalar"),
        )
        for text, construct in cases:
            with self.subTest(text=text):
                with self.assertRaises(compose_subset.SubsetError) as caught:
                    compose_subset.check_subset(text)
                self.assertEqual(caught.exception.construct, construct)
                self.assertEqual(caught.exception.line, 1)

    def test_comment_on_the_document_start_line_is_allowed(self):
        self.assertIsNone(compose_subset.check_subset("--- # compose\nservices: {}\n"))


class IncludeTests(unittest.TestCase):
    def test_top_level_include_is_rejected(self):
        text = "services:\n  app:\n    image: nginx\ninclude: extra.yaml\n"
        with self.assertRaises(compose_subset.SubsetError) as caught:
            compose_subset.check_subset(text)
        self.assertEqual(caught.exception.construct, "include")
        self.assertEqual(caught.exception.line, 4)

    def test_nested_include_is_allowed(self):
        text = "services:\n  app:\n    include: extra.yaml\n"
        self.assertIsNone(compose_subset.check_subset(text))


class ExtendsTests(unittest.TestCase):
    def test_service_level_extends_is_rejected(self):
        text = "services:\n  app:\n    extends: base\n"
        with self.assertRaises(compose_subset.SubsetError) as caught:
            compose_subset.check_subset(text)
        self.assertEqual(caught.exception.construct, "extends")
        self.assertEqual(caught.exception.line, 3)

    def test_sequence_item_extends_is_rejected(self):
        text = "services:\n  - extends: base\n"
        with self.assertRaises(compose_subset.SubsetError) as caught:
            compose_subset.check_subset(text)
        self.assertEqual(caught.exception.construct, "extends")
        self.assertEqual(caught.exception.line, 2)

    def test_top_level_extends_is_allowed(self):
        self.assertIsNone(compose_subset.check_subset("extends: base\n"))


class ProfilesTests(unittest.TestCase):
    def test_service_level_profiles_is_rejected(self):
        text = "services:\n  app:\n    profiles: [dev]\n"
        with self.assertRaises(compose_subset.SubsetError) as caught:
            compose_subset.check_subset(text)
        self.assertEqual(caught.exception.construct, "profiles")
        self.assertEqual(caught.exception.line, 3)

    def test_sequence_item_profiles_is_rejected(self):
        text = "services:\n  - profiles: [dev]\n"
        with self.assertRaises(compose_subset.SubsetError) as caught:
            compose_subset.check_subset(text)
        self.assertEqual(caught.exception.construct, "profiles")
        self.assertEqual(caught.exception.line, 2)

    def test_top_level_profiles_is_allowed(self):
        self.assertIsNone(compose_subset.check_subset("profiles:\n  - dev\n"))


class FlowReferenceTests(unittest.TestCase):
    def test_alias_sequence_item(self):
        with self.assertRaises(compose_subset.SubsetError) as caught:
            compose_subset.check_subset("command: [*a]\n")
        self.assertEqual(caught.exception.construct, "alias")
        self.assertEqual(caught.exception.line, 1)

    def test_alias_in_flow_map_value(self):
        with self.assertRaises(compose_subset.SubsetError) as caught:
            compose_subset.check_subset("command: {k: *a}\n")
        self.assertEqual(caught.exception.construct, "alias")
        self.assertEqual(caught.exception.line, 1)

    def test_alias_after_comma(self):
        with self.assertRaises(compose_subset.SubsetError) as caught:
            compose_subset.check_subset("command: [b, *a]\n")
        self.assertEqual(caught.exception.construct, "alias")
        self.assertEqual(caught.exception.line, 1)

    def test_anchor_in_flow_sequence(self):
        with self.assertRaises(compose_subset.SubsetError) as caught:
            compose_subset.check_subset("command: [&a x]\n")
        self.assertEqual(caught.exception.construct, "anchor")
        self.assertEqual(caught.exception.line, 1)

    def test_anchor_in_flow_map_value(self):
        with self.assertRaises(compose_subset.SubsetError) as caught:
            compose_subset.check_subset("command: {k: &a x}\n")
        self.assertEqual(caught.exception.construct, "anchor")
        self.assertEqual(caught.exception.line, 1)

    def test_quoted_references_are_allowed(self):
        self.assertIsNone(
            compose_subset.check_subset('command: ["*a", b]\n')
        )
        self.assertIsNone(
            compose_subset.check_subset('command: ["a && b"]\n')
        )
        self.assertIsNone(
            compose_subset.check_subset("command: [echo, '*']\n")
        )


class PlainFileTests(unittest.TestCase):
    def test_plain_compose_file_passes(self):
        text = (
            "---\n"
            "services:\n"
            "  app:\n"
            "    image: nginx\n"
            "    ports:\n"
            "      - \"8080:80\"\n"
            "    environment:\n"
            "      - FOO=bar\n"
            "      - BAZ=${FOO:-dev}\n"
            "    command: [\"nginx\", \"-g\", \"daemon off;\"]\n"
        )
        self.assertIsNone(compose_subset.check_subset(text))


if __name__ == "__main__":
    unittest.main()
