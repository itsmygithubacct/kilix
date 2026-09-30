"""Private installer joins; synthetic model bytes, no package or model download.

The content authority's actual receipts are covered by test_content_models.
This fixture tests how the runtime consumes its verification result.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _env_support import sandbox_env

ROOT = Path(__file__).resolve().parents[1]


class RuntimeReuse(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="yolox-private-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.runtime = self.root / "runtime"
        self.source = self.root / "module's source"
        self.host = self.root / "host"
        self.assets = self.root / "apps"
        self.config = self.root / "config"
        self.prepare_source(self.source)
        (self.host / "config").mkdir(parents=True)
        (self.host / "config/yolox_runtime.py").symlink_to(ROOT / "config/yolox_runtime.py")
        self.executable(self.host / "kilix", '''import json,os,pathlib,sys
root=pathlib.Path(os.environ['PRIVATE_ASSETS'])
command,model=sys.argv[2:4]
if command=='show': print(json.dumps({'root':str(root)}))
elif command=='verify':
    sys.exit(0 if (root/'verified').exists() else 1)
else: raise SystemExit('fixture must never install content')
''')
        for model in ("yolox_s", "yolox_nano", "yolox_tiny"):
            directory = self.assets / "assets" / model
            (directory / "notices").mkdir(parents=True)
            (directory / f"{model}.onnx").write_bytes(model.encode())
            (directory / "notices/LICENSE-apache-2.0.txt").write_bytes(b"fixture notice")
        (self.assets / "verified").touch()
        python = self.runtime / "venv/bin/python"
        python.parent.mkdir(parents=True)
        self.executable(python, '''import os,sys
if sys.argv[1]=='-c': raise SystemExit(0)
if sys.argv[1]=='-m': raise SystemExit(int(os.environ.get('PRIVATE_PIP_FAIL','0')))
os.execv(sys.executable,[sys.executable,*sys.argv[1:]])
''')
        self.env = sandbox_env(HOME=str(self.root / "home"), GPU_TERMINAL_HOME=str(self.root / "stack"),
                               GPU_TERMINAL_DATA_HOME=str(self.root / "data"), KILIX_HOME=str(self.host),
                               KILIX_YOLOX_DIR=str(self.runtime), KILIX_YOLOX_SRC=str(self.source),
                               KILIX_USER_CONFIG_DIRECTORY=str(self.config), PRIVATE_ASSETS=str(self.assets),
                               KILIX_YOLOX_TRUST_EXISTING_CHECKOUT="1", KILIX_UV="private-absent-uv")

    def executable(self, path, source):
        path.write_text(f"#!{sys.executable}\n" + source)
        path.chmod(0o700)

    def prepare_source(self, source):
        (source / "tools").mkdir(parents=True)
        (source / "models").mkdir()
        self.executable(source / "tools/kilix-yolox-detect", '''import json,os,sys
print(json.dumps({'argv':sys.argv,'runtime':os.environ['KILIX_YOLOX_DIR']}))
''')
        self.executable(source / "tools/kilix-yolox-cut", '''import pathlib,sys
original=pathlib.Path(sys.argv[1]);size=sys.argv[3]
original.with_name(original.stem+'_'+size+'.onnx').write_bytes(b'private-cut:'+original.read_bytes())
''')
        (source / "models/SHA256SUMS").write_text("".join(
            f"{hashlib.sha256(model.encode()).hexdigest()}  {model}.onnx\n"
            for model in ("yolox_s", "yolox_nano", "yolox_tiny")))

    def run_installer(self, action="--install", model="yolox_s", **extra):
        env = dict(self.env, KILIX_YOLOX_MODEL=model, **extra)
        return subprocess.run([str(ROOT / "scripts/install-yolox.sh"), action, "--yes"],
                              env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=20)

    def install(self, model="yolox_s", **extra):
        result = self.run_installer(model=model, **extra)
        self.assertEqual(result.returncode, 0, result.stderr)
        return self.runtime / "bin/kilix-yolox-detect"

    def invoke(self, wrapper, *args):
        return json.loads(subprocess.check_output([str(wrapper), *args], env=self.env, text=True, timeout=10))

    def test_reinstall_selects_requested_model_and_preserves_literal_arguments(self):
        wrapper = self.install()
        self.install("yolox_nano")
        result = self.invoke(wrapper, "--width", "320", "quote' and space;")
        self.assertEqual(result["argv"][1:], ["--model", "yolox_nano", "--width", "320", "quote' and space;"])
        self.assertEqual(result["runtime"], str(self.runtime))
        self.assertIn(f"KILIX_OBJECT_DETECTOR={wrapper}\n", (self.config / "kilix.env").read_text())

    def test_reinstall_repoints_wrapper_to_new_source(self):
        wrapper = self.install()
        source = self.root / "new source's module"
        self.prepare_source(source)
        self.install(KILIX_YOLOX_SRC=str(source))
        self.assertEqual(self.invoke(wrapper)["argv"][0], str(source / "tools/kilix-yolox-detect"))

    def test_existing_runtime_and_catalog_files_do_not_replace_agreement(self):
        self.install()
        (self.assets / "verified").unlink()
        before = (self.runtime / "runtime.json").read_bytes()
        self.assertEqual(self.run_installer("--check").returncode, 1)
        result = self.run_installer()
        self.assertEqual(result.returncode, 1)
        self.assertIn("typed agreement", result.stderr)
        self.assertEqual((self.runtime / "runtime.json").read_bytes(), before)

    def test_changed_cut_and_export_refuse_readiness_and_reinstall_repairs(self):
        self.install()
        for name in ("yolox_s_320.onnx", "yolox_s.onnx"):
            with self.subTest(file=name):
                (self.runtime / "models" / name).write_bytes(b"changed")
                self.assertEqual(self.run_installer("--check").returncode, 1)
                self.install()
                self.assertEqual(self.run_installer("--check").returncode, 0)

    def test_legacy_runtime_without_binding_is_reprepared(self):
        self.install()
        (self.runtime / "runtime.json").unlink()
        (self.runtime / "models/yolox_s_320.onnx").write_bytes(b"unbound legacy cut")
        self.assertEqual(self.run_installer("--check").returncode, 1)
        self.install()
        self.assertEqual((self.runtime / "models/yolox_s_320.onnx").read_bytes(), b"private-cut:yolox_s")

    def test_cut_tool_change_requires_repreparation(self):
        self.install()
        with (self.source / "tools/kilix-yolox-cut").open("a") as output:
            output.write("\n# changed input\n")
        self.assertEqual(self.run_installer("--check").returncode, 1)
        self.install()
        self.assertEqual(self.run_installer("--check").returncode, 0)

    def test_failed_package_upgrade_does_not_refresh_wrapper_or_binding(self):
        wrapper = self.install()
        before = (wrapper.read_bytes(), (self.runtime / "runtime.json").read_bytes())
        self.assertEqual(self.run_installer("--upgrade", PRIVATE_PIP_FAIL="1").returncode, 1)
        self.assertEqual((wrapper.read_bytes(), (self.runtime / "runtime.json").read_bytes()), before)

    def test_upgrade_retains_valid_binding_and_selected_model(self):
        wrapper = self.install("yolox_nano")
        self.assertEqual(self.run_installer("--upgrade", model="yolox_nano").returncode, 0)
        self.assertEqual(self.run_installer("--check", model="yolox_nano").returncode, 0)
        self.assertEqual(self.invoke(wrapper)["argv"][1:], ["--model", "yolox_nano"])

    def test_changed_wrapper_refuses_readiness_and_is_repaired(self):
        wrapper = self.install()
        wrapper.write_text("#!/bin/sh\nexit 0\n")
        self.assertEqual(self.run_installer("--check").returncode, 1)
        self.install()
        self.assertEqual(self.invoke(wrapper)["argv"][1:], ["--model", "yolox_s"])

    def test_ambiguous_runtime_path_and_unsupported_cut_size_refuse_before_setup(self):
        for extra in ({"KILIX_YOLOX_DIR": str(self.root / "runtime with space")},
                      {"KILIX_YOLOX_SIZE": "321"}):
            with self.subTest(extra=extra):
                self.assertEqual(self.run_installer(**extra).returncode, 1)
                self.assertFalse(self.config.exists())

    def test_check_and_print_path_are_read_only_when_verified(self):
        self.install()
        before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in self.root.rglob("*") if p.is_file()}
        self.assertEqual(self.run_installer("--check").returncode, 0)
        self.assertEqual(self.run_installer("--print-path").returncode, 0)
        self.assertEqual(before, {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in self.root.rglob("*") if p.is_file()})
