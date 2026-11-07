#!/usr/bin/env python3
"""Add whisper-medium-en to a pinned kilix-voice library.

The voice pin still forces every Whisper selection onto whisper-small-en.
RC5 keeps that pin and rewrites the staged library so medium English is a
second Whisper model. Its licence record is faster-whisper-medium-en, with
small.en's MIT terms (OpenAI weights, SYSTRAN CTranslate2 conversion).

A tree without those markers is left unchanged and this exits 0, so the
voice installer fixtures are unaffected. A tree that has the markers but
not these exact lines exits 1.
"""

from __future__ import annotations

import sys
from pathlib import Path


MARKER = 'WHISPER_MODEL = "whisper-small-en"'

# (path name, old, new). old is absent once new is present.
REPLACEMENTS = {
    "models.py": (
        (
            '''    ModelSpec(
        "whisper-small-en",
        ENGINE_WHISPER,
        486100128,
        True,
        "Whisper small English through the kilix-whisper-stt provider: far "
        "fewer errors than the Vosk models on natural dictation, transcribes "
        "when the turn ends (about a second per sentence on a laptop CPU)",
    ),
    ModelSpec(
        "whistle", ENGINE_WHISTLE, 16937647, True,''',
            '''    ModelSpec(
        "whisper-small-en",
        ENGINE_WHISPER,
        486100128,
        True,
        "Whisper small English through the kilix-whisper-stt provider: far "
        "fewer errors than the Vosk models on natural dictation, transcribes "
        "when the turn ends (about a second per sentence on a laptop CPU)",
    ),
    ModelSpec(
        "whisper-medium-en",
        ENGINE_WHISPER,
        1530459085,
        True,
        "Whisper medium English through the kilix-whisper-stt provider: "
        "slower than whisper-small-en, and fewer errors on hard speech",
    ),
    ModelSpec(
        "whistle", ENGINE_WHISTLE, 16937647, True,''',
        ),
        (
            'WHISPER_MODEL = "whisper-small-en"\nWHISTLE_MODEL = "whistle"\n',
            'WHISPER_MODEL = "whisper-small-en"\n'
            'WHISPER_MEDIUM_MODEL = "whisper-medium-en"\n'
            'WHISTLE_MODEL = "whistle"\n',
        ),
        (
            'CONTENT_STORED_MODELS = {WHISPER_MODEL: "faster-whisper-small-en", WHISTLE_MODEL: "whistle"}',
            "CONTENT_STORED_MODELS = {\n"
            '    WHISPER_MODEL: "faster-whisper-small-en",\n'
            '    WHISPER_MEDIUM_MODEL: "faster-whisper-medium-en",\n'
            '    WHISTLE_MODEL: "whistle",\n'
            "}",
        ),
        (
            '    "WHISPER_MODEL",\n',
            '    "WHISPER_MODEL",\n    "WHISPER_MEDIUM_MODEL",\n',
        ),
    ),
    "stt.py": (
        (
            """    if engine in _SINGLE_MODEL:
        # VibeVoice and Whisper have exactly one model each. Choosing the
        # engine alone (a settings screen may offer only that) must not load
        # the default vosk model's directory into it, so the engine decides
        # the model here.
        model_id = _SINGLE_MODEL[engine]
""",
            """    if engine in _SINGLE_MODEL:
        # VibeVoice and Whistle have one model each. Whisper has small and
        # medium English; a catalogued whisper id is kept, and anything else
        # (including a bare engine choice) still opens whisper-small-en.
        # Choosing the engine alone must not load the default vosk directory.
        # model_id already holds the configuration's stt.model or, failing
        # that, the settings file's choice.
        whisper_choice = (
            engine == ENGINE_WHISPER
            and model_id in models.MODEL_BY_ID
            and models.MODEL_BY_ID[model_id].engine == ENGINE_WHISPER
        )
        if not whisper_choice:
            model_id = _SINGLE_MODEL[engine]
""",
        ),
    ),
    "licensing.py": (
        (
            '''    "whisper-small-en": "faster-whisper-small-en",
    "whistle": "whistle",
''',
            '''    "whisper-small-en": "faster-whisper-small-en",
    "whisper-medium-en": "faster-whisper-medium-en",
    "whistle": "whistle",
''',
        ),
        (
            'LICENCE_RECORD_ID = {"whisper-small-en": "faster-whisper-small-en"}',
            "LICENCE_RECORD_ID = {\n"
            '    "whisper-small-en": "faster-whisper-small-en",\n'
            "    # Medium English has its own record, with small.en's MIT terms.\n"
            '    "whisper-medium-en": "faster-whisper-medium-en",\n'
            "}",
        ),
    ),
}


def voicelib(root: Path) -> Path | None:
    """Return the voicelib directory, or None when this tree has none."""
    for candidate in (
        root / "lib" / "kilix-voice" / "voicelib",
        root / "voicelib",
    ):
        if (candidate / "models.py").is_file():
            return candidate
    return None


def apply_pair(text: str, old: str, new: str, label: str) -> str:
    if new in text:
        return text
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label} matched {count} times; expected the pinned line once")
    return text.replace(old, new, 1)


def patch_tree(root: Path) -> bool:
    """Rewrite root when it is the pinned voice library. Return True if changed."""
    library = voicelib(root)
    if library is None:
        return False
    models_path = library / "models.py"
    models = models_path.read_text(encoding="utf-8")
    if MARKER not in models:
        return False
    changed = False
    for name, pairs in REPLACEMENTS.items():
        path = library / name
        if not path.is_file():
            raise SystemExit(f"pinned voice library is missing {path}")
        text = path.read_text(encoding="utf-8")
        updated = text
        for index, (old, new) in enumerate(pairs):
            updated = apply_pair(updated, old, new, f"{name} replacement {index}")
        if updated != text:
            path.write_text(updated, encoding="utf-8")
            changed = True
            cache = library / "__pycache__"
            if cache.is_dir():
                for cached in cache.glob(f"{name[:-3]}.*.pyc"):
                    cached.unlink()
    return changed


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        raise SystemExit(f"usage: {argv[0]} PREFIX")
    root = Path(argv[1])
    if not root.is_dir():
        raise SystemExit(f"not a directory: {root}")
    if patch_tree(root):
        print(f"whisper-medium-en added under {root}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
