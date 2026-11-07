"""The voice-pin patcher adds whisper-medium-en and ignores fixture trees."""

from __future__ import annotations

import importlib.util
import subprocess
import tempfile
import textwrap
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATCHER = ROOT / "scripts" / "patch-whisper-medium-model.py"


def load_patcher():
    spec = importlib.util.spec_from_file_location("patch_whisper_medium_model", PATCHER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PatchWhisperMediumTests(unittest.TestCase):
    def setUp(self):
        self.module = load_patcher()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def write_pinned(self) -> Path:
        library = self.root / "lib" / "kilix-voice" / "voicelib"
        library.mkdir(parents=True)
        for name, pairs in self.module.REPLACEMENTS.items():
            text = "".join(old for old, _new in pairs)
            (library / name).write_text(text, encoding="utf-8")
        return library

    def test_a_tree_without_the_markers_is_left_alone(self):
        empty = self.root / "empty"
        empty.mkdir()
        self.assertFalse(self.module.patch_tree(empty))
        fixture = self.root / "fixture" / "lib" / "kilix-voice" / "voicelib"
        fixture.mkdir(parents=True)
        (fixture / "models.py").write_text("fixture model\n", encoding="utf-8")
        before = (fixture / "models.py").read_text(encoding="utf-8")
        self.assertFalse(self.module.patch_tree(self.root / "fixture"))
        self.assertEqual((fixture / "models.py").read_text(encoding="utf-8"), before)

    def test_the_pinned_library_gains_medium_once(self):
        library = self.write_pinned()
        self.assertTrue(self.module.patch_tree(self.root))
        models = (library / "models.py").read_text(encoding="utf-8")
        stt = (library / "stt.py").read_text(encoding="utf-8")
        licensing = (library / "licensing.py").read_text(encoding="utf-8")
        self.assertIn('"whisper-medium-en"', models)
        self.assertIn("1530459085", models)
        self.assertIn('WHISPER_MEDIUM_MODEL: "faster-whisper-medium-en"', models)
        self.assertIn("whisper_choice", stt)
        self.assertEqual(licensing.count('"whisper-medium-en": "faster-whisper-medium-en"'), 2)
        self.assertNotIn('"whisper-medium-en": "faster-whisper-small-en"', licensing)
        self.assertIn(
            "LICENCE_RECORD_ID = {\n"
            '    "whisper-small-en": "faster-whisper-small-en",\n'
            "    # Medium English has its own record, with small.en's MIT terms.\n"
            '    "whisper-medium-en": "faster-whisper-medium-en",\n'
            "}",
            licensing,
        )
        self.assertFalse(self.module.patch_tree(self.root))
        self.assertEqual((library / "models.py").read_text(encoding="utf-8"), models)

    def test_a_whisper_model_chosen_in_the_settings_file_is_kept(self):
        # Seat B (2026-11-07): the patched block read only the passed
        # configuration's stt.model, so whisper-medium-en chosen in the
        # settings file alone fell back to whisper-small-en. resolve_stt has
        # merged both sources into model_id before this block runs.
        ((_old, new),) = self.module.REPLACEMENTS["stt.py"]
        block = textwrap.dedent(new)
        whisper = types.SimpleNamespace(engine="whisper")
        models = types.SimpleNamespace(MODEL_BY_ID={
            "whisper-small-en": whisper, "whisper-medium-en": whisper,
            "small-en-us": types.SimpleNamespace(engine="vosk")})

        def resolve(model_id, config=None, engine="whisper"):
            scope = {"engine": engine, "model_id": model_id, "config": config or {},
                     "cfg_get": lambda cfg, key: cfg.get(key), "models": models,
                     "ENGINE_WHISPER": "whisper",
                     "_SINGLE_MODEL": {"whisper": "whisper-small-en", "whistle": "whistle"}}
            exec(block, scope)
            return scope["model_id"]

        self.assertEqual(resolve("whisper-medium-en"), "whisper-medium-en")
        self.assertEqual(resolve("whisper-medium-en", {"stt.model": "whisper-medium-en"}),
                         "whisper-medium-en")
        self.assertEqual(resolve("whisper-small-en"), "whisper-small-en")
        self.assertEqual(resolve(""), "whisper-small-en")
        self.assertEqual(resolve("small-en-us"), "whisper-small-en")
        self.assertEqual(resolve("not-a-model"), "whisper-small-en")
        self.assertEqual(resolve("whisper-medium-en", engine="whistle"), "whistle")

    def test_the_script_exits_quietly_when_the_prefix_is_a_fixture(self):
        prefix = self.root / "prefix"
        prefix.mkdir()
        result = subprocess.run(
            ["python3", str(PATCHER), str(prefix)],
            check=False, capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertEqual(result.stdout, "")


if __name__ == "__main__":
    unittest.main()
