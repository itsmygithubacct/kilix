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
        self.spec = SimpleNamespace(asset_id="lfm2-kilix-commands")
        self.catalog = SimpleNamespace(require_asset=lambda asset: self.spec)

    def run_ensure(self, asset, verify, install, *, tty=False, auto="1", root="/tmp/kilix-apps"):
        with ExitStack() as stack:
            stack.enter_context(patch.object(ui, "_api", return_value=object()))
            stack.enter_context(patch.object(ui, "_license", return_value=object()))
            stack.enter_context(patch.object(ui, "_verified_catalog", return_value=self.catalog))
            stack.enter_context(patch.object(ui, "apps_root", return_value=root))
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

    def test_a_no_is_declined_and_end_of_input_is_not(self):
        # ensure exits 3 for Declined, which the Ask offer remembers. Input
        # that ends at the question is no answer and must not be remembered.
        def verify(*args):
            raise ui.SetupError("missing")

        with self.assertRaises(ui.Declined):
            self.run_ensure("lfm2-kilix-commands", verify, lambda *args: 1, tty=True)

        def ended(*args):
            import io
            return ui._read_line("Install? [y/N] ", 64, io.StringIO(""), io.StringIO())

        with self.assertRaises(ui.Unanswered) as raised:
            self.run_ensure("lfm2-kilix-commands", verify, ended, tty=True)
        self.assertNotIsInstance(raised.exception, ui.Declined)
        import io
        self.assertEqual(ui._read_line("? ", 64, io.StringIO("\n"), io.StringIO()), "")
        self.assertFalse(ui._answer("? ", "y", io.StringIO("n\n"), io.StringIO()))

    def test_a_receipt_whose_files_never_arrived_goes_on_to_the_install(self):
        # A download that fails after the terms were shown leaves a receipt
        # and no files. Verifying then raises the installer's error, not
        # SetupError; that is "not installed", not a reason to stop.
        class InstallError(RuntimeError):
            pass

        seen = []

        def verify(*args):
            seen.append("verify")
            if seen.count("verify") == 1:
                raise InstallError("could not open supplied file model.gguf")
            return 0

        with patch.object(ui, "_install_error", return_value=InstallError):
            self.run_ensure("lfm2-kilix-commands", verify,
                            lambda *args: seen.append("install") or 0, tty=True)
        self.assertEqual(seen, ["verify", "install", "verify"])
        for error in (OSError("unreadable"),):
            seen.clear()

            def broken(*args, error=error):
                seen.append("verify")
                if seen.count("verify") == 1:
                    raise error
                return 0
            self.run_ensure("lfm2-kilix-commands", broken,
                            lambda *args: seen.append("install") or 0, tty=True)
            self.assertEqual(seen, ["verify", "install", "verify"])

    def test_a_damaged_install_is_named_and_never_reaches_the_install_question(self):
        # The installer refuses to replace a directory that fails verification,
        # so a yes there could not succeed and a no would be remembered.
        import tempfile
        with tempfile.TemporaryDirectory() as root:
            self.spec.install_name = "lfm2-kilix-commands--" + "a" * 64
            present = Path(root, "assets", self.spec.install_name)
            for error in (ui.SetupError("installed model file differs from its manifest: x"),
                          OSError("could not open supplied file")):
                def verify(*args, error=error):
                    raise error
                install = lambda *args: (_ for _ in ()).throw(AssertionError("asked"))
                for tty in (True, False):
                    present.mkdir(parents=True, exist_ok=True)
                    with patch.object(ui, "apps_root", return_value=root), \
                            self.assertRaises(ui.Damaged) as raised:
                        self.run_ensure("lfm2-kilix-commands", verify, install, tty=tty,
                                        root=root)
                    self.assertIn(str(present), str(raised.exception))
                    self.assertIn(str(error), str(raised.exception))
                    self.assertNotIsInstance(raised.exception, ui.Declined)
                # The same failures with nothing at the path go on to the install.
                present.rmdir()
                seen = []

                def missing(*args, error=error):
                    seen.append("verify")
                    if seen.count("verify") == 1:
                        raise error
                    return 0
                self.run_ensure("lfm2-kilix-commands", missing,
                                lambda *args: seen.append("install") or 0, tty=True, root=root)
                self.assertEqual(seen, ["verify", "install", "verify"])

    def test_intact_files_without_a_receipt_go_to_the_install_not_to_damaged(self):
        # No receipt says nothing about the files. Telling the user to remove
        # a verified install would be wrong; the install shows the terms and
        # the installer checks what is on disk.
        import tempfile
        with tempfile.TemporaryDirectory() as root:
            self.spec.install_name = "lfm2-kilix-commands--" + "a" * 64
            Path(root, "assets", self.spec.install_name).mkdir(parents=True)
            seen = []

            def verify(*args):
                seen.append("verify")
                if seen.count("verify") == 1:
                    raise ui.NoReceipt("no stored receipt covers this exact model and manifest")
                return 0
            self.run_ensure("lfm2-kilix-commands", verify,
                            lambda *args: seen.append("install") or 0, tty=True, root=root)
            self.assertEqual(seen, ["verify", "install", "verify"])
            self.assertTrue(issubclass(ui.NoReceipt, ui.SetupError))
            self.assertFalse(issubclass(ui.NoReceipt, ui.Damaged))

    def test_auto_install_off_refuses_on_a_terminal(self):
        def verify(*args):
            raise ui.SetupError("missing")

        with self.assertRaisesRegex(ui.SetupError, "run kilix models install"):
            self.run_ensure("qwen3.5-4b", verify,
                            lambda *args: (_ for _ in ()).throw(AssertionError("installed")),
                            tty=True, auto="0")


class EarlierDeliveryWordingTests(unittest.TestCase):
    """The Content installer reuses an earlier delivery; this file only says so."""

    def facts(self, installer):
        spec = SimpleNamespace(asset_id="lfm2-kilix-commands", label="LFM2", version="v10",
                               download_bytes=10, installed_bytes=20)
        api = SimpleNamespace(Installer=installer)
        with patch.object(ui, "_api", return_value=api), \
                patch.object(ui, "_verified_catalog",
                             return_value=SimpleNamespace(require_asset=lambda asset: spec)):
            return ui.first_use_facts("lfm2-kilix-commands", root="/tmp/kilix-apps")

    def test_facts_name_the_directory_the_installer_would_reuse(self):
        seen = []

        class Installer:
            def __init__(self, root):
                seen.append(root)

            def earlier_delivery(self, spec):
                return "/tmp/kilix-apps/assets/lfm2-kilix-commands--old"
        facts = self.facts(Installer)
        self.assertEqual(seen, ["/tmp/kilix-apps"])
        self.assertEqual(facts["reuse"], "/tmp/kilix-apps/assets/lfm2-kilix-commands--old")
        self.assertEqual((facts["download_bytes"], facts["installed_bytes"]), (10, 20))

    def test_a_component_without_reuse_or_a_failing_one_means_expect_a_download(self):
        class Old:
            def __init__(self, root):
                pass

        class Broken(Old):
            def earlier_delivery(self, spec):
                raise OSError("unreadable")
        self.assertIsNone(self.facts(Old)["reuse"])
        self.assertIsNone(self.facts(Broken)["reuse"])


if __name__ == "__main__":
    unittest.main()
