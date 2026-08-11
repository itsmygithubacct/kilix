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

    def __init__(self, content_id, label, kind="app", icon="", description="",
                 source_type="git", binary="tool", launch_mode="terminal",
                 preferred_size="", capabilities=(), package_id="",
                 command=(), actions=(), accepts=(), lifecycle=None):
        self.content_id = content_id
        self.label = label
        self.kind = kind
        self.icon = icon
        self.description = description
        self.source_type = source_type
        self.package_id = package_id
        self.install_id = package_id or content_id
        self.binary = binary
        self.command = command
        self.launch_mode = launch_mode
        self.preferred_size = preferred_size
        self.capabilities = capabilities
        self.actions = actions
        self.accepts = accepts
        self.lifecycle = lifecycle or content.LifecycleSpec()

    def require_action(self, action_id):
        for action in self.actions:
            if action.action_id == action_id:
                return action
        raise content.CatalogError(
            f"{self.content_id}: unknown application action {action_id!r}")


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
                                        "description", "source_type", "binary",
                                        "command", "actions", "accepts", "lifecycle",
                                        "package_id", "install_id",
                                        "launch_mode", "preferred_size",
                                        "capabilities"})

    def test_launch_metadata_is_plain_and_complete(self):
        spec = _Spec("a", "A", preferred_size="760x520",
                     capabilities=("network",))
        rec = content.menu_records(FakeCatalog([spec]))[0]
        self.assertEqual(rec["launch_mode"], "terminal")
        self.assertEqual(rec["preferred_size"], "760x520")
        self.assertEqual(rec["capabilities"], ["network"])

    def test_shared_package_identity_is_plain_metadata(self):
        spec = _Spec("files", "Files", package_id="kilix-tui-utils")
        rec = content.menu_records(FakeCatalog([spec]))[0]
        self.assertEqual(rec["package_id"], "kilix-tui-utils")
        self.assertEqual(rec["install_id"], "kilix-tui-utils")

    def test_actions_inputs_and_lifecycle_are_plain_metadata(self):
        action = content.ActionSpec("open", ("--open",), True, "Open a file")
        lifecycle = content.LifecycleSpec(
            single_instance=True,
            requires_kilix_session=True,
            startup_timeout_seconds=12,
        )
        spec = _Spec(
            "files", "Files", command=("kilix", "files"),
            actions=(action,), accepts=("text/plain",), lifecycle=lifecycle,
        )
        rec = content.menu_records(FakeCatalog([spec]))[0]
        self.assertEqual(rec["command"], ["kilix", "files"])
        self.assertEqual(rec["actions"]["open"]["argv"], ["--open"])
        self.assertTrue(rec["actions"]["open"]["accepts_input"])
        self.assertEqual(rec["accepts"], ["text/plain"])
        self.assertTrue(rec["lifecycle"]["single_instance"])
        self.assertEqual(rec["lifecycle"]["startup_timeout_seconds"], 12)


class TestApplicationPlan(unittest.TestCase):
    class _Catalog(FakeCatalog):
        def require(self, content_id):
            for spec in self._specs:
                if spec.content_id == content_id:
                    return spec
            raise content.CatalogError(f"unknown content id: {content_id}")

    def test_terminal_app_maps_to_current_pane_or_window(self):
        catalog = self._Catalog([
            _Spec("pdf", "PDF Conversion", icon="doc_text",
                  preferred_size="760x520")])
        current = content.application_plan(
            "pdf", "current", catalog=catalog, launcher="/kilix")
        pane = content.application_plan(
            "pdf", "pane", ["report.pdf"], catalog=catalog,
            launcher="/kilix")
        window = content.application_plan(
            "pdf", "window", catalog=catalog, launcher="/kilix")
        self.assertEqual(current.argv, ("/kilix", "app", "run", "pdf"))
        self.assertEqual(
            pane.argv,
            ("/kilix", "app", "run", "pdf", "--", "report.pdf"))
        self.assertEqual(window.argv, ("/kilix", "app", "window", "pdf"))
        self.assertEqual(window.preferred_size, (760, 520))
        self.assertEqual(window.icon, "doc_text")

    def test_native_x_app_runs_directly_on_a_window_surface(self):
        catalog = self._Catalog([
            _Spec("amp", "Amp", launch_mode="xpane",
                  preferred_size="600x360")])
        plan = content.application_plan(
            "amp", "window", catalog=catalog, launcher="/kilix")
        self.assertEqual(plan.argv, ("/kilix", "app", "run", "amp"))

    def test_system_app_uses_its_catalog_identity_as_the_program(self):
        catalog = self._Catalog([
            _Spec("dosbox", "DOSBox", source_type="system", binary="")])
        plan = content.application_plan("dosbox", catalog=catalog)
        self.assertEqual(plan.argv, ("dosbox",))

    def test_system_command_uses_the_selected_host_launcher(self):
        catalog = self._Catalog([
            _Spec("models", "Models", source_type="system", binary="",
                  command=("kilix", "bonsai"))])
        plan = content.application_plan(
            "models", catalog=catalog, launcher="/host/kilix")
        self.assertEqual(plan.argv, ("/host/kilix", "bonsai"))
        window = content.application_plan(
            "models", "window", catalog=catalog, launcher="/host/kilix")
        self.assertEqual(
            window.argv,
            ("/host/kilix", "app", "window", "models"),
        )

    def test_named_actions_add_only_fixed_argv_and_one_declared_input(self):
        open_action = content.ActionSpec("open", ("--open",), True, "")
        catalog = self._Catalog([
            _Spec("files", "Files", actions=(open_action,),
                  accepts=("text/plain",))])
        plan = content.application_plan(
            "files", "pane", ["notes.txt"], catalog=catalog,
            launcher="/kilix", action="open")
        self.assertEqual(
            plan.argv,
            ("/kilix", "app", "run", "files", "--action", "open", "--",
             "notes.txt"),
        )
        self.assertEqual(plan.action, "open")
        self.assertEqual(plan.accepts, ("text/plain",))
        with self.assertRaises(ValueError):
            content.application_plan(
                "files", arguments=["one", "two"], catalog=catalog,
                action="open")

    def test_lifecycle_policy_reaches_the_host_plan(self):
        lifecycle = content.LifecycleSpec(
            single_instance=True,
            requires_kilix_session=True,
            degrades_inplace=False,
            preserve_on_failure=False,
            startup_timeout_seconds=20,
        )
        catalog = self._Catalog([_Spec("session", "Session", lifecycle=lifecycle)])
        plan = content.application_plan("session", catalog=catalog)
        self.assertTrue(plan.single_instance)
        self.assertTrue(plan.requires_kilix_session)
        self.assertFalse(plan.degrades_inplace)
        self.assertFalse(plan.preserve_on_failure)
        self.assertEqual(plan.startup_timeout_seconds, 20)

    def test_host_owned_dosbox_uses_its_existing_shared_launcher(self):
        catalog = self._Catalog([
            _Spec("dosbox", "DOSBox", source_type="custom",
                  launch_mode="run", preferred_size="640x400")])
        plan = content.application_plan(
            "dosbox", "window", catalog=catalog, launcher="/kilix")
        self.assertEqual(
            plan.argv, ("/kilix", "games", "play", "dosbox"))
        self.assertEqual(plan.preferred_size, (640, 400))

    def test_games_and_unknown_surfaces_are_rejected(self):
        games = self._Catalog([_Spec("pong", "Pong", kind="game")])
        with self.assertRaises(content.CatalogError):
            content.application_plan("pong", catalog=games)
        with self.assertRaises(ValueError):
            content.application_plan("pong", "box", catalog=games)


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

    def test_every_shipped_application_has_surface_and_action_plans(self):
        applications = [
            spec for spec in self.catalog if spec.kind == "app"
        ]
        self.assertGreater(len(applications), 0)
        for spec in applications:
            for surface in ("current", "pane", "window"):
                with self.subTest(application=spec.content_id, surface=surface):
                    plan = content.application_plan(
                        spec.content_id,
                        surface,
                        catalog=self.catalog,
                        launcher="/fixture/kilix",
                    )
                    self.assertEqual(plan.content_id, spec.content_id)
                    self.assertEqual(plan.surface, surface)
                    self.assertTrue(plan.argv)
            for action in spec.actions:
                inputs = ["fixture-input"] if action.accepts_input else []
                for surface in ("current", "pane", "window"):
                    with self.subTest(
                        application=spec.content_id,
                        action=action.action_id,
                        surface=surface,
                    ):
                        plan = content.application_plan(
                            spec.content_id,
                            surface,
                            inputs,
                            catalog=self.catalog,
                            launcher="/fixture/kilix",
                            action=action.action_id,
                        )
                        self.assertEqual(plan.action, action.action_id)
                        self.assertTrue(plan.argv)


if __name__ == "__main__":
    unittest.main()
