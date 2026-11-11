"""Models a catalog application needs for one call.

Listing, doctor, and the other read-only verbs install nothing. The calls that
synthesize, separate, or convert install the models they name, and only those.
This module does not show a licence and does not download. The caller does,
through ``kilix models ensure``.
"""
from __future__ import annotations

import os
import sys

import playalong_models

PDF_TEXT = ("pp-ocrv6-small-det", "pp-ocrv6-small-rec")
PDF_GRANITE = "granite-docling-258m"
PDF_CHART = "documentfigureclassifier-v2.5"
_ENGLISH = {"", "en", "auto", "english"}


def _words(arguments) -> list[str]:
    words = [str(item) for item in arguments]
    if words[:1] == ["--"]:
        words = words[1:]
    return words


def _command(words: list[str]) -> str:
    if words[:1] == ["--action"] and len(words) > 1:
        return words[1]
    return words[0] if words else ""


def _language(words: list[str]) -> str | None:
    """The ``--language`` value, or None when the call does not set one."""
    previous = None
    value = None
    seen = False
    for word in words:
        if previous == "--language":
            value = word
            seen = True
        elif word.startswith("--language="):
            value = word.split("=", 1)[1]
            seen = True
        previous = word
    if not seen:
        return None
    return value or ""


def _wants_large(words: list[str]) -> bool:
    language = _language(words)
    if language is not None and language.casefold() not in _ENGLISH:
        return True
    previous = None
    for word in words:
        if word in {"--model=large-v3", "--whisper=large-v3"}:
            return True
        if previous in {"--model", "--whisper"} and word == "large-v3":
            return True
        previous = word
    return False


def _pdf_models(words: list[str]) -> tuple[str, ...]:
    command = _command(words)
    if command not in {"convert", "batch"}:
        return ()
    assets = list(PDF_TEXT)
    if any(word.startswith("granite") for word in words):
        assets.append(PDF_GRANITE)
    if "--chart-extraction" in words:
        assets.append(PDF_CHART)
    return tuple(assets)


def _playalong_models(words: list[str], *, declined, unreadable: bool) -> tuple[str, ...]:
    if unreadable or _command(words) not in {"create", "resume"}:
        return ()
    assets: list[str] = []
    if declined is None:
        assets.extend(playalong_models.DEFAULT_MODELS)
    if _wants_large(words):
        assets.append(playalong_models.OPTIONAL_MODELS[0])
    return tuple(assets)


def models_for_call(content_id: str, arguments, *, declined=None,
                    unreadable: bool = False) -> tuple[str, ...]:
    """Return the catalog ids this call needs. An empty result installs nothing.

    ``declined`` is the reason Playalong's defaults were refused, or None when
    they were not. ``unreadable`` means that reason could not be read: install
    nothing rather than guess. Whisper large-v3 is not a default. A Playalong
    ``models`` command is the explicit installer and is not repeated here.
    """
    words = _words(arguments)
    if content_id == "playalong":
        return _playalong_models(words, declined=declined, unreadable=unreadable)
    if content_id == "kilix-pdf-conversion":
        return _pdf_models(words)
    return ()


def _first_use() -> bool:
    value = os.environ.get("KILIX_MODEL_AUTO_INSTALL")
    if value is not None and value.strip().casefold() in {"0", "no", "false", "off"}:
        return False
    try:
        return sys.stdin.isatty() and sys.stdout.isatty()
    except (AttributeError, OSError, ValueError):
        return False


def ensure_for_launch(content_id: str, arguments) -> None:
    """Ensure the models for a run. A declined licence raises SetupError.

    No terminal, or ``KILIX_MODEL_AUTO_INSTALL=0``, returns without raising so
    the application can show its own message. An unreadable Playalong answer
    installs nothing and does not stop the launch. ``install`` and ``ref`` do
    not call this.
    """
    if content_id == "playalong":
        if not models_for_call(content_id, arguments):
            return
        try:
            reason = playalong_models.declined()
        except (OSError, ValueError, ImportError, RuntimeError):
            return
        assets = models_for_call(content_id, arguments, declined=reason)
    else:
        assets = models_for_call(content_id, arguments)
    if not assets or not _first_use():
        return
    import content_models
    for asset in assets:
        content_models.ensure_installed(asset)
