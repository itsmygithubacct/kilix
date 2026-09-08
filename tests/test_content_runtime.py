"""The installer and every audio launch surface select the same content root."""

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "config"))

import content_app  # noqa: E402
from kilix_sdk import _content_runtime as content_runtime  # noqa: E402
import remote_mux  # noqa: E402


class ContentRuntimeTests(unittest.TestCase):
    def test_root_uses_host_storage_not_xdg_or_inherited_content(self):
        cases = (
            ({"GPU_TERMINAL_HOME": "/relocated /gpu"},
             "/relocated /gpu/kilix/data/desktop-apps"),
            ({"KILIX_STORAGE_HOME": "/storage/../selected"},
             "/selected/data/desktop-apps"),
            ({"KILIX_DATA_HOME": "/data/../selected 'quoted'"},
             "/selected 'quoted'/desktop-apps"),
        )
        for configured, expected in cases:
            with self.subTest(configured=configured), mock.patch.dict(
                os.environ, {
                    "HOME": "/fixture-home", "XDG_DATA_HOME": "/private-app",
                    "KILIX_CONTENT_ROOT": "/stale/receipts", **configured,
                }, clear=True,
            ):
                self.assertEqual(content_runtime.apps_root(), expected)
                self.assertEqual(
                    content_runtime.launch_environment()["KILIX_CONTENT_ROOT"],
                    expected,
                )
                self.assertEqual(os.environ["KILIX_CONTENT_ROOT"], "/stale/receipts")

    def test_tilde_and_relative_host_storage_match_sdk_normalization(self):
        for value, expected in (("~/data", "/fixture-home/data"),
                                ("relative data", os.path.abspath("relative data"))):
            with self.subTest(value=value), mock.patch.dict(
                os.environ, {"HOME": "/fixture-home", "KILIX_DATA_HOME": value},
                clear=True,
            ):
                self.assertEqual(content_runtime.apps_root(), expected + "/desktop-apps")

    def test_explicit_root_is_absolute_and_lexically_normalized(self):
        self.assertEqual(content_runtime.normalized_root(Path("/a/../b")), "/b")
        for invalid in ("relative", "", "/nul\0value", b"/bytes", 1):
            with self.subTest(invalid=invalid), self.assertRaises((ValueError, TypeError)):
                content_runtime.normalized_root(invalid)

    def test_environment_copy_replaces_only_the_content_root(self):
        original = {"KILIX_CONTENT_ROOT": "/stale", "XDG_DATA_HOME": "/private"}
        result = content_runtime.launch_environment(root="/actual/../catalog", base=original)
        self.assertEqual(result, {
            "KILIX_CONTENT_ROOT": "/catalog", "XDG_DATA_HOME": "/private",
        })
        self.assertEqual(original["KILIX_CONTENT_ROOT"], "/stale")

    def test_forwarded_root_matches_the_actual_installer(self):
        with tempfile.TemporaryDirectory() as temporary, mock.patch.dict(
            os.environ, {"KILIX_DATA_HOME": temporary + "/data/../chosen"}, clear=True,
        ):
            forwarded = content_runtime.launch_environment()["KILIX_CONTENT_ROOT"]
            self.assertFalse(Path(forwarded).exists())
            installer = content_app.content.Installer(content_app.apps_root())
            self.assertEqual(forwarded, installer.root)

    def test_catalog_exec_overrides_stale_root_on_both_surfaces(self):
        for surface in ("current", "window"):
            with self.subTest(surface=surface), mock.patch.dict(os.environ, {
                "KILIX_DATA_HOME": "/selected data", "KILIX_CONTENT_ROOT": "/wrong",
            }, clear=True), mock.patch.object(content_app.os, "execvpe") as execute:
                content_app._exec(["/app", "two words"], surface=surface,
                                  content_id="kilix-amp", action="open")
                executable, argv, environment = execute.call_args.args
                self.assertEqual((executable, argv), ("/app", ["/app", "two words"]))
                self.assertEqual(environment["KILIX_CONTENT_ROOT"],
                                 "/selected data/desktop-apps")
                self.assertEqual(environment["KILIX_APP_SURFACE"], surface)
                self.assertEqual(environment["KILIX_APP_ACTION"], "open")

    def test_remote_attach_and_view_bind_root_without_changing_arguments(self):
        for view in (False, True):
            with self.subTest(view=view), mock.patch.dict(os.environ, {
                "KILIX_DATA_HOME": "/selected data", "KILIX_CONTENT_ROOT": "/wrong",
            }, clear=True), mock.patch.object(remote_mux, "build_binary",
                                              return_value="/bin/kmx-attach"), \
                 mock.patch.object(remote_mux.os, "execve") as execute:
                ns = remote_mux.parser().parse_args([
                    "view" if view else "attach", "--socket", "/private/socket",
                    "--no-audio",
                ])
                self.assertEqual(remote_mux.cmd_attach(ns, view=view), 1)
                executable, argv, environment = execute.call_args.args
                self.assertEqual(executable, "/bin/kmx-attach")
                self.assertEqual("--view" in argv, view)
                self.assertIn("--no-audio", argv)
                self.assertEqual(environment["KILIX_CONTENT_ROOT"],
                                 "/selected data/desktop-apps")

    def test_desktop_amp_keeps_private_xdg_but_uses_its_installer_root(self):
        parent = ModuleType("fixture_apps")
        parent.__path__ = []
        xpane = ModuleType("fixture_apps.xpane")
        xpane.XPane = mock.Mock(return_value="window")
        storage = SimpleNamespace(config_dir=lambda _: "/private-config",
                                  data_dir=lambda _: "/private-data")
        modules = {"fixture_apps": parent, "fixture_apps.xpane": xpane,
                   "wm": SimpleNamespace(msgbox=mock.Mock()), "storage": storage,
                   "games": SimpleNamespace(APPS_DIR="/real apps/../catalog")}
        with mock.patch.dict(sys.modules, modules), mock.patch.dict(
            os.environ, {"KILIX_CONTENT_ROOT": "/wrong"}, clear=True,
        ):
            spec = importlib.util.spec_from_file_location(
                "fixture_apps.amp", ROOT / "desktop/apps/amp.py")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            desk = SimpleNamespace(wm=SimpleNamespace(add=mock.Mock()))
            with mock.patch.object(module, "_seed_sample"):
                module._spawn(desk, "/installed/amp", "song name.wav")
            environment = xpane.XPane.call_args.kwargs["env"]
            self.assertEqual(environment, {
                "XDG_CONFIG_HOME": "/private-config", "XDG_DATA_HOME": "/private-data",
                "KILIX_CONTENT_ROOT": "/catalog",
            })
            desk.wm.add.assert_called_once_with("window")

    def test_amp_print_root_is_read_only_and_uses_shared_resolution(self):
        with tempfile.TemporaryDirectory() as temporary:
            selected = str(Path(temporary) / "not-created 'quoted'")
            environment = {"PATH": os.defpath, "HOME": temporary,
                           "KILIX_DATA_HOME": selected, "KILIX_CONTENT_ROOT": "/wrong",
                           "PYTHONDONTWRITEBYTECODE": "1"}
            result = subprocess.run([
                sys.executable, str(ROOT / "scripts/install-kilix-amp.py"), "--print-root",
            ], env=environment, capture_output=True, text=True, timeout=10, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, selected + "/desktop-apps\n")
            self.assertFalse(Path(selected).exists())

    def test_shell_amp_dispatch_preserves_quoted_root_and_headless_arguments(self):
        # Run the exact dispatch block. Only application installation is replaced
        # by a fixture; root resolution executes the real shared Python helper.
        launcher = (ROOT / "kilix").read_text()
        block = launcher.split("  amp|media-player)\n", 1)[1].split(
            "  app|application)\n", 1)[0]
        with tempfile.TemporaryDirectory() as temporary:
            fixture = Path(temporary)
            executable = fixture / "fake-amp"
            executable.write_text(
                "#!/usr/bin/python3\nimport json,os,sys\n"
                "print(json.dumps({'argv':sys.argv[1:],'root':os.environ.get('KILIX_CONTENT_ROOT')}))\n")
            executable.chmod(0o700)
            script = (
                "set -eu\n"
                "python3() {\n"
                "  if [ \"${2:-}\" = --print-root ]; then\n"
                "    command python3 \"$@\"\n"
                "  else printf '%s\\n' \"$FIXTURE_AMP\"; fi\n}\n"
                "_kilix_ensure_private_directory() { return 0; }\n"
                "case \"$1\" in\namp|media-player)\n" + block + "esac\n"
            )
            for extra in ([], ["--headless"], ["--install-only"]):
                with self.subTest(extra=extra):
                    selected = str(fixture / "data 'with' $characters")
                    result = subprocess.run(["bash", "-c", script, "fixture",
                        "amp", *extra, "--", "song name.wav"], env={
                        "PATH": os.defpath, "HOME": temporary, "KILIX_HOME": str(ROOT),
                        "KILIX_DATA_HOME": selected, "KILIX_CONTENT_ROOT": "/wrong",
                        "KILIX_SESSION_HOME": str(fixture / "session"),
                        "FIXTURE_AMP": str(executable), "PYTHONDONTWRITEBYTECODE": "1",
                    }, capture_output=True, text=True, timeout=10, check=False)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    if extra == ["--install-only"]:
                        self.assertEqual(result.stdout, "")
                    else:
                        self.assertEqual(json.loads(result.stdout), {
                            "root": selected + "/desktop-apps",
                            "argv": [*extra, "song name.wav"],
                        })


if __name__ == "__main__":
    unittest.main()
