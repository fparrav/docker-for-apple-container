from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from container_docker_shim import compose  # noqa: E402
from container_docker_shim import compose_plan  # noqa: E402

TIER = "unit"


def project(**services):
    """A project whose services map name -> depends_on (list or map form)."""
    return compose.Project(
        "demo",
        {"services": {name: {"image": "x", "depends_on": deps} for name, deps in services.items()}},
        "/tmp/demo",
    )


class SelectionTests(unittest.TestCase):
    def test_direct_dependency_excludes_the_unrelated_service(self):
        proj = project(api=["db"], db=None, worker=None)
        self.assertEqual(compose_plan.plan(proj, ["api"]).ordered, ["db", "api"])

    def test_transitive_dependencies_come_first(self):
        proj = project(web=["api"], api=["db"], db=None)
        self.assertEqual(compose_plan.plan(proj, ["web"]).ordered, ["db", "api", "web"])

    def test_diamond_creates_the_shared_dependency_once(self):
        proj = project(top=["left", "right"], left=["base"], right=["base"], base=None)
        ordered = compose_plan.plan(proj, ["top"]).ordered
        self.assertEqual(sorted(ordered), ["base", "left", "right", "top"])
        self.assertEqual(ordered.count("base"), 1)
        self.assertLess(ordered.index("base"), ordered.index("left"))
        self.assertLess(ordered.index("left"), ordered.index("top"))
        self.assertLess(ordered.index("right"), ordered.index("top"))

    def test_whole_project_when_nothing_is_named(self):
        proj = project(api=["db"], db=None, worker=None)
        self.assertEqual(compose_plan.plan(proj).ordered, ["db", "api", "worker"])
        self.assertEqual(compose_plan.plan(proj, []).ordered, ["db", "api", "worker"])

    def test_several_selected_services_are_ordered_together(self):
        proj = project(api=["db"], db=None, worker=["api"])
        self.assertEqual(compose_plan.plan(proj, ["worker", "api"]).ordered, ["db", "api", "worker"])

    def test_no_deps_acts_on_the_named_services_only(self):
        proj = project(api=["db"], db=None)
        result = compose_plan.plan(proj, ["api"], no_deps=True)
        self.assertEqual(result.ordered, ["api"])
        self.assertEqual(len(result.notices), 1)
        self.assertIn("db", result.notices[0])

    def test_no_deps_without_missing_dependencies_has_no_notice(self):
        proj = project(api=["db"], db=None)
        self.assertEqual(compose_plan.plan(proj, ["api", "db"], no_deps=True).notices, [])


class OptionalDependencyTests(unittest.TestCase):
    def test_optional_dependency_is_not_pulled_in(self):
        proj = project(api={"cache": {"required": False}}, cache=None)
        self.assertEqual(compose_plan.plan(proj, ["api"]).ordered, ["api"])

    def test_optional_dependency_present_in_the_plan_is_ordered_first(self):
        proj = project(api={"cache": {"required": False}}, cache=None)
        self.assertEqual(compose_plan.plan(proj, ["api", "cache"]).ordered, ["cache", "api"])

    def test_optional_dependency_on_an_undefined_service_exits_64(self):
        proj = project(api={"ghost": {"required": False}})
        with self.assertRaises(compose.ShimError) as caught:
            compose_plan.plan(proj, ["api"])
        self.assertEqual(caught.exception.code, 64)
        self.assertIn("'ghost'", str(caught.exception))

    def test_required_dependency_is_pulled_in_even_when_another_is_optional(self):
        proj = project(api={"db": {}, "cache": {"required": False}}, db=None, cache=None)
        self.assertEqual(compose_plan.plan(proj, ["api"]).ordered, ["db", "api"])


class InvalidGraphTests(unittest.TestCase):
    def test_cycle_names_the_services_and_exits_64(self):
        proj = project(a=["b"], b=["a"])
        with self.assertRaises(compose.ShimError) as caught:
            compose_plan.plan(proj)
        self.assertEqual(caught.exception.code, 64)
        self.assertIn("'a'", str(caught.exception))
        self.assertIn("'b'", str(caught.exception))

    def test_cycle_outside_the_selection_is_still_rejected(self):
        proj = project(a=["b"], b=["a"], c=None)
        with self.assertRaises(compose.ShimError) as caught:
            compose_plan.plan(proj, ["c"])
        self.assertEqual(caught.exception.code, 64)

    def test_required_dependency_on_an_undefined_service_exits_64(self):
        proj = project(api=["db"])
        with self.assertRaises(compose.ShimError) as caught:
            compose_plan.plan(proj, ["api"])
        self.assertEqual(caught.exception.code, 64)
        self.assertIn("'api'", str(caught.exception))
        self.assertIn("'db'", str(caught.exception))

    def test_no_deps_still_rejects_an_undefined_dependency(self):
        proj = project(api=["ghost"], worker=None)
        with self.assertRaises(compose.ShimError) as caught:
            compose_plan.plan(proj, ["worker"], no_deps=True)
        self.assertEqual(caught.exception.code, 64)
        self.assertIn("'ghost'", str(caught.exception))

    def test_unknown_selected_name_exits_1(self):
        proj = project(api=None)
        with self.assertRaises(compose.ShimError) as caught:
            compose_plan.plan(proj, ["apii"])
        self.assertEqual(caught.exception.code, 1)
        self.assertIn("apii", str(caught.exception))

    def test_all_unknown_names_are_reported(self):
        proj = project(api=None)
        with self.assertRaises(compose.ShimError) as caught:
            compose_plan.plan(proj, ["x", "api", "y"])
        self.assertIn("x", str(caught.exception))
        self.assertIn("y", str(caught.exception))


class PurityTests(unittest.TestCase):
    def test_module_imports_no_runtime_helpers(self):
        source = (ROOT / "src" / "container_docker_shim" / "compose_plan.py").read_text()
        self.assertNotIn("_run_container", source)
        self.assertNotIn("subprocess", source)


if __name__ == "__main__":
    unittest.main()
