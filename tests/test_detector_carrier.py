"""Offline detector source delivery through Content's carrier CLI."""

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _env_support import sandbox_env  # noqa: E402

IDENTITY = {"GIT_AUTHOR_NAME": "kilix tests", "GIT_AUTHOR_EMAIL": "tests@example.invalid",
            "GIT_COMMITTER_NAME": "kilix tests", "GIT_COMMITTER_EMAIL": "tests@example.invalid"}
HELPER = Path(os.environ.get("KILIX_TEST_SOURCE_CARRIER_HELPER", str(
    ROOT / "third_party/kilix-content/src/kilix_content/source_carrier.py")))


def git(path, *args):
    env = sandbox_env(**IDENTITY)
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_TERMINAL_PROMPT="0")
    return subprocess.run(["git", "-C", str(path), *args], env=env,
                          capture_output=True, text=True, check=True).stdout.strip()


def init(path):
    path.mkdir(parents=True)
    subprocess.run(["git", "init", "--quiet", "-b", "main", str(path)], check=True)


def commit(path, message):
    git(path, "add", "-A")
    git(path, "commit", "--quiet", "-m", message)
    return git(path, "rev-parse", "HEAD")


@unittest.skipUnless(HELPER.is_file(), "requires Content's source carrier implementation")
class DetectorCarrierInstall(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="detector-carrier-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.host = self.root / "host"
        (self.host / "scripts").mkdir(parents=True)
        shutil.copyfile(ROOT / "scripts/detector-carrier.sh", self.host / "scripts/detector-carrier.sh")
        helper = self.host / "third_party/kilix-content/src/kilix_content/source_carrier.py"
        helper.parent.mkdir(parents=True)
        shutil.copyfile(HELPER, helper)
        self.carrier = self.root / "carrier"
        (self.carrier / "bundles").mkdir(parents=True)
        child = self.root / "child"
        init(child)
        (child / "data").write_text("old dependency\n")
        old_child = commit(child, "old child")
        (child / "data").write_text("new dependency\n")
        new_child = commit(child, "new child")
        self.dep_url = "https://fixture.invalid/child.git"
        self.revisions = {}
        nodes = []

        def node(repo, revision, url, name, links):
            bundle = self.carrier / "bundles" / (name + ".bundle")
            if not bundle.exists():
                git(repo, "bundle", "create", str(bundle), "--all")
            raw = bundle.read_bytes()
            return {"repository": url, "commit": revision,
                    "tree": git(repo, "rev-parse", revision + "^{tree}"),
                    "bundle": "bundles/" + bundle.name, "bytes": len(raw),
                    "sha256": hashlib.sha256(raw).hexdigest(), "gitlinks": links}

        nodes.extend([node(child, old_child, self.dep_url, "child", {}),
                      node(child, new_child, self.dep_url, "child", {})])
        for component, prefix, binary in [("object-detect", "LOOK", "kilix-look"),
                                          ("nvr", "NVR", "kilix-nvr")]:
            repo = self.root / (component + "-origin")
            init(repo)
            (repo / ".gitignore").write_text("build/\nlocal-data/\n")
            (repo / "Makefile").write_text(
                "all:\n\tmkdir -p build\n\tcp marker build/" + binary
                + "\n\tchmod +x build/" + binary + "\n")
            (repo / ".gitmodules").write_text(
                '[submodule "fixture"]\n path = third_party/child\n url = '
                + self.dep_url + "\n")
            (repo / "marker").write_text("#!/bin/sh\n# old\n")
            git(repo, "add", "-A")
            git(repo, "update-index", "--add", "--cacheinfo",
                "160000," + old_child + ",third_party/child")
            git(repo, "commit", "--quiet", "-m", "old source")
            old = git(repo, "rev-parse", "HEAD")
            (repo / "marker").write_text("#!/bin/sh\n# new\n")
            git(repo, "add", "marker")
            git(repo, "update-index", "--cacheinfo",
                "160000," + new_child + ",third_party/child")
            git(repo, "commit", "--quiet", "-m", "new source")
            new = git(repo, "rev-parse", "HEAD")
            url = "https://fixture.invalid/" + component + ".git"
            for revision, child_revision in [(old, old_child), (new, new_child)]:
                nodes.append(node(repo, revision, url, component,
                                  {"third_party/child": {"repository": self.dep_url,
                                                         "commit": child_revision}}))
            self.revisions[component] = (prefix, binary, url, old, new)
        yolox = self.root / "yolox-origin"
        init(yolox)
        (yolox / "tools").mkdir()
        (yolox / "models").mkdir()
        (yolox / ".gitignore").write_text("local-data/\n")
        (yolox / "tools/kilix-yolox-detect").write_text(
            "#!/usr/bin/env python3\nimport sys\nprint('fixture detector')\n")
        (yolox / "tools/kilix-yolox-cut").write_text(
            "import pathlib,sys\np=pathlib.Path(sys.argv[1]); "
            "p.with_name(p.stem+'_'+sys.argv[3]+'.onnx').write_bytes(b'cut:'+p.read_bytes())\n")
        (yolox / "models/SHA256SUMS").write_text("".join(
            hashlib.sha256(name.encode()).hexdigest() + "  " + name + ".onnx\n"
            for name in ("yolox_s", "yolox_nano", "yolox_tiny")))
        (yolox / "marker").write_text("old\n")
        yolo_old = commit(yolox, "old YOLOX")
        (yolox / "marker").write_text("new\n")
        yolo_new = commit(yolox, "new YOLOX")
        yolo_url = "https://fixture.invalid/kilix-yolox.git"
        nodes.extend([node(yolox, yolo_old, yolo_url, "yolox", {}),
                      node(yolox, yolo_new, yolo_url, "yolox", {})])
        self.yolox = (yolo_url, yolo_old, yolo_new)
        self.manifest = self.carrier / "manifest.json"
        self.manifest.write_text(json.dumps({"schema": "kilix.source-carrier/v1", "nodes": nodes}))
        self.digest = hashlib.sha256(self.manifest.read_bytes()).hexdigest()
        self.receipt = self.root / "existing-receipt.json"
        self.receipt.write_text('{"decision":"existing fixture"}\n')

    def run_installer(self, component, revision, **overrides):
        prefix, _binary, url, _old, _new = self.revisions[component]
        env = sandbox_env(**IDENTITY)
        env.update({"KILIX_HOME": str(self.host),
                    "GPU_TERMINAL_SOURCE_HOME": str(self.root / "stack"),
                    "KILIX_LOOK_HOME": str(self.root / "look-state"),
                    "KILIX_SOUND_HOME": str(self.root / "sound-state"),
                    "XDG_DATA_HOME": str(self.root / "xdg-data"),
                    "XDG_CACHE_HOME": str(self.root / "xdg-cache"),
                    "XDG_STATE_HOME": str(self.root / "xdg-state"),
                    "XDG_CONFIG_HOME": str(self.root / "xdg-config"),
                    "KILIX_" + prefix + "_DIR": str(self.root / (component + " installed")),
                    "KILIX_" + prefix + "_REPO": url,
                    "KILIX_" + prefix + "_REF": revision,
                    "KILIX_SOURCE_CARRIER_MANIFEST": str(self.manifest),
                    "KILIX_SOURCE_CARRIER_SHA256": self.digest,
                    "GIT_ALLOW_PROTOCOL": "file"})
        env.update(overrides)
        script = ROOT / "scripts" / ("install-kilix-" + component + ".sh")
        return subprocess.run([str(script)], env=env, text=True, capture_output=True, timeout=120)

    def test_fresh_update_and_corrupt_carrier_preserve_installed_state(self):
        for component in self.revisions:
            with self.subTest(component=component):
                _prefix, binary, _url, old, new = self.revisions[component]
                installed = self.root / (component + " installed")
                bundle = self.carrier / "bundles/child.bundle"
                original = bundle.read_bytes()
                bundle.write_bytes(original + b"tampered")
                failed_fresh = self.run_installer(component, old)
                self.assertNotEqual(failed_fresh.returncode, 0)
                self.assertIn("offline source carrier refused", failed_fresh.stderr)
                self.assertFalse(installed.exists())
                bundle.write_bytes(original)
                fresh = self.run_installer(component, old)
                self.assertEqual(fresh.returncode, 0, fresh.stderr)
                self.assertEqual(git(installed, "rev-parse", "HEAD"), old)
                self.assertIn("# old", (installed / "build" / binary).read_text())
                (installed / "local-data").mkdir()
                (installed / "local-data/keep").write_text("user data\n")
                git(installed, "branch", "user-ref", old)
                before = (installed / "build" / binary).read_bytes()
                receipt = self.receipt.read_bytes()
                kept = self.run_installer(
                    component, new, **{"KILIX_" + _prefix + "_KEEP_EXISTING_CHECKOUT": "1"})
                self.assertEqual(kept.returncode, 0, kept.stderr)
                self.assertEqual(git(installed, "rev-parse", "HEAD"), old)
                self.assertIn("NOT installed", kept.stderr)
                bundle.unlink()
                refused = self.run_installer(component, new)
                self.assertNotEqual(refused.returncode, 0)
                self.assertEqual(git(installed, "rev-parse", "HEAD"), old)
                self.assertEqual((installed / "build" / binary).read_bytes(), before)
                self.assertEqual((installed / "local-data/keep").read_text(), "user data\n")
                self.assertEqual(self.receipt.read_bytes(), receipt)
                bundle.write_bytes(original)
                update = self.run_installer(component, new)
                self.assertEqual(update.returncode, 0, update.stderr)
                self.assertEqual(git(installed, "rev-parse", "HEAD"), new)
                self.assertIn("# new", (installed / "build" / binary).read_text())
                self.assertEqual((installed / "local-data/keep").read_text(), "user data\n")
                self.assertEqual(git(installed, "rev-parse", "user-ref"), old)
                self.assertTrue((installed / "third_party/child/data").read_text().startswith("new"))
                self.assertEqual(git(installed, "config", "--local", "--get",
                                     "submodule.fixture.url"), self.dep_url)
                self.assertEqual(git(installed / "third_party/child", "config", "--local", "--get",
                                     "remote.origin.url"), self.dep_url)
                self.assertEqual(self.receipt.read_bytes(), receipt)

    def test_empty_explicit_configuration_and_missing_helper_fail_closed(self):
        helper = self.host / "third_party/kilix-content/src/kilix_content/source_carrier.py"
        for component in self.revisions:
            with self.subTest(component=component):
                _prefix, _binary, _url, old, _new = self.revisions[component]
                destination = self.root / (component + " installed")
                empty = self.run_installer(component, old, KILIX_SOURCE_CARRIER_MANIFEST="",
                                           KILIX_SOURCE_CARRIER_SHA256="")
                self.assertNotEqual(empty.returncode, 0)
                self.assertIn("offline source carrier refused", empty.stderr)
                self.assertFalse(destination.exists())
                saved = helper.read_bytes()
                helper.unlink()
                try:
                    missing = self.run_installer(component, old)
                    self.assertNotEqual(missing.returncode, 0)
                    self.assertIn("Content helper is unavailable", missing.stderr)
                    self.assertFalse(destination.exists())
                finally:
                    helper.write_bytes(saved)

    def test_yolox_fresh_update_and_missing_carrier_preserve_runtime_and_receipt(self):
        url, old, new = self.yolox
        source = self.root / "yolox source"
        runtime = self.root / "runtime"
        (self.host / "config").mkdir()
        shutil.copyfile(ROOT / "config/yolox_runtime.py", self.host / "config/yolox_runtime.py")
        apps = self.root / "apps"
        for name in ("yolox_s", "yolox_nano", "yolox_tiny"):
            asset = apps / "assets" / name
            (asset / "notices").mkdir(parents=True)
            (asset / (name + ".onnx")).write_bytes(name.encode())
            (asset / "notices/LICENSE-apache-2.0.txt").write_text("fixture licence\n")
        (apps / "verified").touch()
        cli = self.host / "kilix"
        cli.write_text("#!" + sys.executable + "\nimport json,os,pathlib,sys\n"
                       "root=pathlib.Path(os.environ['FIXTURE_APPS'])\n"
                       "cmd=sys.argv[2]\n"
                       "if cmd=='show': print(json.dumps({'root':str(root)}))\n"
                       "elif cmd=='verify': sys.exit(0 if (root/'verified').exists() else 1)\n"
                       "else: sys.exit(1)\n")
        cli.chmod(0o700)
        python = runtime / "venv/bin/python"
        python.parent.mkdir(parents=True)
        python.write_text("#!" + sys.executable + "\nimport os,sys\n"
                          "if sys.argv[1] in ('-c','-m'): sys.exit(0)\n"
                          "os.execv(sys.executable,[sys.executable,*sys.argv[1:]])\n")
        python.chmod(0o700)
        env = sandbox_env(**IDENTITY)
        env.update({"KILIX_HOME": str(self.host), "GPU_TERMINAL_DATA_HOME": str(self.root / "data"),
                    "GPU_TERMINAL_SOURCE_HOME": str(self.root / "stack"),
                    "KILIX_LOOK_HOME": str(self.root / "look-state"),
                    "KILIX_SOUND_HOME": str(self.root / "sound-state"),
                    "XDG_DATA_HOME": str(self.root / "xdg-data"),
                    "XDG_CACHE_HOME": str(self.root / "xdg-cache"),
                    "XDG_STATE_HOME": str(self.root / "xdg-state"),
                    "XDG_CONFIG_HOME": str(self.root / "xdg-config"),
                    "KILIX_YOLOX_DIR": str(runtime), "KILIX_YOLOX_SRC": str(source),
                    "KILIX_YOLOX_REPO": url, "KILIX_YOLOX_REF": old,
                    "KILIX_YOLOX_MODEL": "yolox_nano",
                    "KILIX_USER_CONFIG_DIRECTORY": str(self.root / "config"),
                    "KILIX_SOURCE_CARRIER_MANIFEST": str(self.manifest),
                    "KILIX_SOURCE_CARRIER_SHA256": self.digest,
                    "KILIX_UV": "fixture-absent-uv", "FIXTURE_APPS": str(apps),
                    "GIT_ALLOW_PROTOCOL": "file"})
        script = ROOT / "scripts/install-yolox.sh"

        def install():
            return subprocess.run([str(script), "--install", "--yes"], env=env,
                                  capture_output=True, text=True, timeout=120)

        fresh = install()
        self.assertEqual(fresh.returncode, 0, fresh.stderr)
        self.assertEqual(git(source, "rev-parse", "HEAD"), old)
        self.assertEqual(json.loads((runtime / "runtime.json").read_text())["model"], "yolox_nano")
        before = (runtime / "runtime.json").read_bytes()
        receipt = self.receipt.read_bytes()
        env["KILIX_YOLOX_REF"] = new
        bundle = self.carrier / "bundles/yolox.bundle"
        data = bundle.read_bytes()
        bundle.unlink()
        refused = install()
        self.assertNotEqual(refused.returncode, 0)
        self.assertEqual(git(source, "rev-parse", "HEAD"), old)
        self.assertEqual((runtime / "runtime.json").read_bytes(), before)
        self.assertEqual(self.receipt.read_bytes(), receipt)
        bundle.write_bytes(data)
        updated = install()
        self.assertEqual(updated.returncode, 0, updated.stderr)
        self.assertEqual(git(source, "rev-parse", "HEAD"), new)
        self.assertEqual(git(source, "config", "--local", "--get", "remote.origin.url"), url)
        self.assertEqual(json.loads((runtime / "runtime.json").read_text())["source_revision"], new)
        self.assertEqual(json.loads((runtime / "runtime.json").read_text())["model"], "yolox_nano")
        self.assertEqual(self.receipt.read_bytes(), receipt)


if __name__ == "__main__":
    unittest.main()
