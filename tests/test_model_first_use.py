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


if __name__ == "__main__":
    unittest.main()
