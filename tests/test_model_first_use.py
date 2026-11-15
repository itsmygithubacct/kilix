"""First use installs a catalog model only when a terminal can answer."""
from contextlib import ExitStack
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "config"))
import content_models as ui  # noqa: E402


class EnsureInstalledTests(unittest.TestCase):
    def setUp(self):
        self.spec = SimpleNamespace(asset_id="lfm2-kilix-commands",
                                    install_name="lfm2-kilix-commands", files=[])
        self.catalog = SimpleNamespace(require_asset=lambda asset: self.spec)

    def run_ensure(self, asset, verify, install, *, tty=False, auto="1"):
        with ExitStack() as stack:
            stack.enter_context(patch.object(ui, "_api", return_value=object()))
            stack.enter_context(patch.object(ui, "_license", return_value=object()))
            stack.enter_context(patch.object(ui, "_verified_catalog", return_value=self.catalog))
            stack.enter_context(patch.object(ui, "apps_root", return_value="/tmp/kilix-apps"))
            stack.enter_context(patch.object(ui, "_verify", side_effect=verify))
            held = stack.enter_context(patch.object(ui, "_install", side_effect=install))
            stack.enter_context(patch.object(sys.stdin, "isatty", return_value=tty))
            stack.enter_context(patch.object(sys.stdout, "isatty", return_value=tty))
            stack.enter_context(patch.dict(os.environ, {"KILIX_MODEL_AUTO_INSTALL": auto}))
            ui.ensure_installed(asset)
            return held

    def test_a_verified_model_is_not_installed_again(self):
        calls = []
        held = self.run_ensure(
            "lfm2-kilix-commands",
            lambda *args: calls.append("verify") or 0,
            lambda *args: calls.append("install") or 0,
            tty=True)
        self.assertEqual(calls, ["verify"])
        held.assert_not_called()

    def test_without_a_terminal_the_install_command_is_named(self):
        def verify(*args):
            raise ui.SetupError("missing")

        with self.assertRaisesRegex(ui.SetupError, "kilix models install lfm2-kilix-commands"):
            self.run_ensure("lfm2-kilix-commands", verify,
                            lambda *args: (_ for _ in ()).throw(AssertionError("installed")),
                            tty=False)

    def test_a_declined_licence_is_not_accepted_for_the_user(self):
        seen = []

        def verify(*args):
            seen.append("verify")
            raise ui.SetupError("missing")

        def install(*args):
            seen.append("install")
            return 1

        with self.assertRaisesRegex(ui.SetupError, "was not installed"):
            self.run_ensure("lfm2-kilix-commands", verify, install, tty=True)
        self.assertEqual(seen, ["verify", "install"])

    def test_auto_install_off_refuses_on_a_terminal(self):
        def verify(*args):
            raise ui.SetupError("missing")

        with self.assertRaisesRegex(ui.SetupError, "run kilix models install"):
            self.run_ensure("qwen3.5-4b", verify,
                            lambda *args: (_ for _ in ()).throw(AssertionError("installed")),
                            tty=True, auto="0")


class EarlierDeliveryTests(unittest.TestCase):
    """A new delivery is built from the previous one on disk, with no download."""

    NEW, OLD = "b" * 64, "a" * 64

    def setUp(self):
        import tempfile
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        member = lambda path, size: SimpleNamespace(path=path, bytes=size)  # noqa: E731
        self.spec = SimpleNamespace(
            asset_id="lfm2-kilix-commands", install_name="lfm2-kilix-commands--" + self.NEW,
            files=[member("model.gguf", 5), member("runtime/lib.so.0", 3),
                   member("notices/NOTICE.txt", 9)])

    def install(self, digest, *, model=b"model", notice=b"old"):
        path = self.root / "assets" / ("lfm2-kilix-commands--" + digest)
        (path / "runtime").mkdir(parents=True)
        (path / "notices").mkdir()
        (path / "model.gguf").write_bytes(model)
        (path / "runtime/lib.so.0").write_bytes(b"lib")
        (path / "runtime/lib.so").write_bytes(b"lib")     # a duplicate the new delivery dropped
        (path / "notices/NOTICE.txt").write_bytes(notice)  # notices come from the authority
        return str(path)

    def test_a_previous_delivery_with_every_file_at_the_new_size_is_found(self):
        self.assertIsNone(ui.earlier_install(self.spec, str(self.root)))
        old = self.install(self.OLD)
        self.assertEqual(ui.earlier_install(self.spec, str(self.root)), old)

    def test_the_current_install_other_assets_links_and_wrong_sizes_are_not_candidates(self):
        self.install(self.NEW)
        self.install("c" * 64, model=b"short")[:0]
        (self.root / "assets" / ("lfm2-kilix-commands--" + "c" * 64) / "model.gguf").write_bytes(b"x")
        other = self.root / "assets" / ("lfm3-kilix-commands--" + self.OLD)
        other.mkdir()
        (self.root / "assets" / "lfm2-kilix-commands--not-a-digest").mkdir()
        real = Path(self.install("d" * 64))
        moved = real.with_name("moved")
        real.rename(moved)
        real.symlink_to(moved)
        self.assertIsNone(ui.earlier_install(self.spec, str(self.root)))
        linked = Path(self.install("e" * 64)) / "model.gguf"
        linked.unlink()
        linked.symlink_to(moved / "model.gguf")
        self.assertIsNone(ui.earlier_install(self.spec, str(self.root)))

    def test_an_unversioned_asset_has_no_earlier_delivery(self):
        self.install(self.OLD)
        self.spec.install_name = "lfm2-kilix-commands"
        self.assertIsNone(ui.earlier_install(self.spec, str(self.root)))

    def ensure(self, install):
        def verify(*args):
            if not verify.done:
                raise ui.SetupError("missing")
        verify.done = False
        output = __import__("io").StringIO()
        with ExitStack() as stack:
            stack.enter_context(patch.object(ui, "_api", return_value=object()))
            stack.enter_context(patch.object(ui, "_license", return_value=object()))
            stack.enter_context(patch.object(
                ui, "_verified_catalog",
                return_value=SimpleNamespace(require_asset=lambda asset: self.spec)))
            stack.enter_context(patch.object(ui, "_verify", side_effect=verify))
            stack.enter_context(patch.object(ui, "_terminal", return_value=True))
            stack.enter_context(patch.dict(os.environ, {"KILIX_MODEL_AUTO_INSTALL": "1"}))

            def run(api, lic, spec, request, *streams):
                status = install(request)
                verify.done = status == 0
                return status
            stack.enter_context(patch.object(ui, "_install", side_effect=run))
            ui.ensure_installed("lfm2-kilix-commands", root=str(self.root), output=output)
        return output.getvalue()

    def test_first_use_supplies_the_new_delivery_from_the_old_one(self):
        old = self.install(self.OLD)
        seen = []
        self.ensure(lambda request: seen.append((request.supplied, request.reused)) or 0)
        self.assertEqual(seen, [(old, True)])

    def test_without_an_earlier_delivery_first_use_downloads_as_before(self):
        seen = []
        self.ensure(lambda request: seen.append((request.supplied, request.reused)) or 0)
        self.assertEqual(seen, [(None, False)])

    def test_an_unusable_earlier_delivery_is_reported_and_the_ordinary_install_is_shown(self):
        old = self.install(self.OLD)
        seen = []

        def install(request):
            seen.append(request.supplied)
            if request.supplied:
                raise ValueError("supplied file does not match the manifest: model.gguf")
            return 0
        output = self.ensure(install)
        self.assertEqual(seen, [old, None])
        self.assertIn("earlier delivery on this computer could not be used", output)
        self.assertIn("does not match the manifest", output)

    def test_declining_the_reuse_is_a_decline_and_does_not_fall_back_to_a_download(self):
        self.install(self.OLD)
        seen = []
        with self.assertRaisesRegex(ui.SetupError, "was not installed"):
            self.ensure(lambda request: seen.append(request.supplied) or 1)
        self.assertEqual(len(seen), 1)


if __name__ == "__main__":
    unittest.main()
