"""kilix_sdk.content enumeration helpers (S01).

These are what desktops call instead of hand-maintaining an ID table, so the
properties that matter are: every record is returned, the order is settled, and
the shape is stable enough to render without importing the content module.
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "config"))

from kilix_sdk import content  # noqa: E402


class _Spec:
    """Minimal ContentSpec stand-in: these helpers must not depend on the
    install machinery hanging off the real class."""

    def __init__(self, content_id, label, kind="app", icon="", description=""):
        self.content_id = content_id
        self.label = label
        self.kind = kind
        self.icon = icon
        self.description = description


class FakeCatalog:
    def __init__(self, specs):
        self._specs = list(specs)

    def __iter__(self):
        return iter(self._specs)

    def __len__(self):
        return len(self._specs)


class TestEntries(unittest.TestCase):
    def test_returns_every_record(self):
        cat = FakeCatalog([_Spec("a", "Apple"), _Spec("b", "Banana")])
        self.assertEqual(len(content.entries(cat)), 2)

    def test_ordered_by_label_case_insensitively(self):
        cat = FakeCatalog([_Spec("z", "zebra"), _Spec("a", "Apple"),
                           _Spec("m", "mango")])
        self.assertEqual([s.label for s in content.entries(cat)],
                         ["Apple", "mango", "zebra"])

    def test_falls_back_to_id_when_label_is_empty(self):
        cat = FakeCatalog([_Spec("only-id", "")])
        self.assertEqual(content.entries(cat)[0].content_id, "only-id")

    def test_empty_catalog_is_not_an_error(self):
        self.assertEqual(content.entries(FakeCatalog([])), [])


class TestGrouped(unittest.TestCase):
    def test_buckets_by_kind(self):
        cat = FakeCatalog([_Spec("a", "A", "game"), _Spec("b", "B", "app"),
                           _Spec("c", "C", "game")])
        g = content.grouped(cat)
        self.assertEqual(sorted(g), ["app", "game"])
        self.assertEqual([s.label for s in g["game"]], ["A", "C"])

    def test_unknown_kind_is_carried_through_not_dropped(self):
        # A kind added upstream must reach menus without a change here.
        g = content.grouped(FakeCatalog([_Spec("x", "X", "utility")]))
        self.assertIn("utility", g)

    def test_missing_kind_defaults_to_app(self):
        g = content.grouped(FakeCatalog([_Spec("x", "X", "")]))
        self.assertIn("app", g)


class TestMenuRecords(unittest.TestCase):
    def test_shape_is_plain_dicts(self):
        recs = content.menu_records(FakeCatalog([_Spec("a", "Apple", "game")]))
        self.assertEqual(recs[0]["id"], "a")
        self.assertEqual(recs[0]["label"], "Apple")
        self.assertEqual(recs[0]["kind"], "game")
        self.assertIsInstance(recs[0], dict)

    def test_no_spec_objects_leak_into_the_records(self):
        recs = content.menu_records(FakeCatalog([_Spec("a", "A")]))
        self.assertEqual(set(recs[0]), {"id", "label", "kind", "icon",
                                        "description"})


class TestAgainstTheRealCatalog(unittest.TestCase):
    """The helpers must work on the catalog the host actually ships."""

    def setUp(self):
        try:
            self.catalog = content.default_catalog()
        except Exception as exc:  # pragma: no cover - unbuilt submodule
            self.skipTest(f"content catalog unavailable: {exc}")

    def test_enumerates_the_whole_shipped_catalog(self):
        self.assertEqual(len(content.entries(self.catalog)), len(self.catalog))
        self.assertGreater(len(self.catalog), 0)

    def test_every_shipped_record_has_a_usable_label_and_id(self):
        for rec in content.menu_records(self.catalog):
            self.assertTrue(rec["id"])
            self.assertTrue(rec["label"])

    def test_default_catalog_is_used_when_none_is_passed(self):
        self.assertEqual(len(content.entries()), len(self.catalog))


if __name__ == "__main__":
    unittest.main()
