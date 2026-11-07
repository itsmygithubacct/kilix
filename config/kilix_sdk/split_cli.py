"""Split a mix into stems with a private Demucs install.

``--install`` creates ``$KILIX_DATA_HOME/split/venv`` and installs the pinned
Demucs. Separation runs that venv's Python. A ``.wav`` ``--output`` receives
one stem. Any other output path is a directory of ``<stem>.wav`` files.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

STEMS = ("vocals", "drums", "bass", "guitar", "piano", "other")
MODEL = "htdemucs_6s"
DEVICES = ("auto", "cpu", "cuda")
# Pinned separator. Installed with this exact git URL, not from PyPI.
DEMUCS_URL = "git+https://github.com/adefossez/demucs.git@eeac1d15891af95b1288d2884b95baa3e5baa96c"
NOT_INSTALLED = "kilix split: Demucs is not installed. Run: kilix split --install"
OFFLINE_PROXY = "http://127.0.0.1:9"


class SplitParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise SystemExit(f"kilix split: {message}")


def data_root() -> Path:
    value = os.environ.get("KILIX_DATA_HOME")
    if value and value.strip():
        return Path(os.path.abspath(os.path.expanduser(value)))
    return Path.home() / ".local" / "gpu_terminal" / "kilix" / "data"


def absolute(path: Path) -> Path:
    return Path(os.path.abspath(os.path.expanduser(str(path))))


def ensure_private(path: Path) -> Path:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.chmod(0o700)
    return path


def venv_python() -> Path | None:
    bindir = data_root() / "split" / "venv" / "bin"
    for name in ("python", "python3"):
        candidate = bindir / name
        if candidate.is_file():
            return candidate
    return None


def demucs_installed() -> bool:
    python = venv_python()
    if python is None:
        return False
    venv = python.parent.parent
    for libname in ("lib", "lib64"):
        root = venv / libname
        if not root.is_dir():
            continue
        for site in root.glob("python*/site-packages"):
            package = site / "demucs" / "__init__.py"
            module = site / "demucs.py"
            if package.is_file() or module.is_file():
                return True
    return False


def require_demucs() -> Path:
    python = venv_python()
    if python is None or not demucs_installed():
        raise SystemExit(NOT_INSTALLED)
    return python


def parse_uv_version(text: str) -> tuple[int, int]:
    for token in text.split():
        if not token or not token[0].isdigit():
            continue
        numbers: list[int] = []
        for part in token.split("."):
            digits = ""
            for character in part:
                if character.isdigit():
                    digits += character
                else:
                    break
            if not digits:
                break
            numbers.append(int(digits))
        if numbers:
            minor = numbers[1] if len(numbers) > 1 else 0
            return numbers[0], minor
    raise SystemExit("kilix split: cannot read the uv version")


def require_uv() -> str:
    uv = shutil.which("uv")
    if not uv:
        raise SystemExit(
            "kilix split: uv is required to install Demucs. "
            "Install uv 0.12 or newer and rerun: kilix split --install")
    try:
        result = subprocess.run(
            [uv, "--version"], check=False, capture_output=True, text=True, errors="replace")
    except OSError as error:
        raise SystemExit(f"kilix split: cannot run uv: {error}") from error
    text = (result.stdout or result.stderr or "").strip()
    if result.returncode != 0:
        detail = text or "uv --version failed"
        raise SystemExit(f"kilix split: cannot run uv: {detail}")
    if parse_uv_version(text) < (0, 12):
        raise SystemExit(
            f"kilix split: uv {text} is older than 0.12. "
            "Install uv 0.12 or newer and rerun: kilix split --install")
    return uv


def run_checked(args: list[str]) -> None:
    try:
        result = subprocess.run(
            args, check=False, capture_output=True, text=True, errors="replace")
    except OSError as error:
        raise SystemExit(f"kilix split: {error}") from error
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "command failed").strip().splitlines()
        raise SystemExit("kilix split: " + (detail[-1] if detail else "command failed"))


def command_install() -> int:
    uv = require_uv()
    root = ensure_private(data_root() / "split")
    venv = root / "venv"
    if venv.exists() and not (venv / "pyvenv.cfg").is_file():
        shutil.rmtree(venv)
    run_checked([uv, "venv", "--python", sys.executable, str(venv)])
    python = venv_python()
    if python is None:
        raise SystemExit("kilix split: uv did not create a virtualenv python")
    run_checked([uv, "pip", "install", "--python", str(python), DEMUCS_URL])
    if not demucs_installed():
        raise SystemExit(NOT_INSTALLED)
    print(f"kilix split: installed Demucs into {venv}", file=sys.stderr)
    return 0


def demucs_cache() -> Path:
    cache = ensure_private(data_root() / "split" / "demucs")
    ensure_private(cache / "torch")
    ensure_private(cache / "huggingface")
    return cache


def child_environment(cache: Path, allow_downloads: bool) -> dict[str, str]:
    environment = dict(os.environ)
    environment["TORCH_HOME"] = str(cache / "torch")
    environment["HF_HOME"] = str(cache / "huggingface")
    environment["HF_HUB_DISABLE_TELEMETRY"] = "1"
    if allow_downloads:
        # A parent shell may already be pinned offline. This flag asks for the weights.
        environment.pop("HF_HUB_OFFLINE", None)
    else:
        environment["HF_HUB_OFFLINE"] = "1"
        environment.update(
            http_proxy=OFFLINE_PROXY,
            https_proxy=OFFLINE_PROXY,
            HTTP_PROXY=OFFLINE_PROXY,
            HTTPS_PROXY=OFFLINE_PROXY,
            ALL_PROXY=OFFLINE_PROXY,
            NO_PROXY="",
        )
    return environment


def read_stems(scratch: Path, model: str) -> dict[str, Path]:
    folder = scratch / model.replace("hf://", "").replace("/", "_")
    found: dict[str, Path] = {}
    if not folder.is_dir():
        return found
    for path in sorted(folder.glob("*.wav")):
        if path.is_file():
            found[path.stem] = path
    return found


def write_private(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    target.chmod(0o600)


def deliver(found: dict[str, Path], output: Path, stem: str) -> None:
    if "vocals" not in found:
        raise SystemExit("kilix split: Demucs did not produce a vocals stem")
    if output.suffix.lower() == ".wav":
        source = found.get(stem)
        if source is None:
            raise SystemExit(f"kilix split: Demucs did not produce a {stem} stem")
        write_private(source, output)
        if not output.is_file():
            raise SystemExit("kilix split: Demucs produced no output")
        return
    if output.exists() and not output.is_dir():
        raise SystemExit(f"kilix split: output is not a directory: {output}")
    output.mkdir(parents=True, exist_ok=True)
    for name in STEMS:
        source = found.get(name)
        if source is None:
            continue
        write_private(source, output / f"{name}.wav")
    if not (output / "vocals.wav").is_file():
        raise SystemExit("kilix split: Demucs produced no output")


def discard(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink(missing_ok=True)
        return
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)


def command_split(audio: Path, output: Path, stem: str, model: str, device: str,
                  allow_downloads: bool) -> int:
    if model != MODEL:
        raise SystemExit(f"kilix split: unknown model {model}")
    if device not in DEVICES:
        raise SystemExit(f"kilix split: unknown device {device}")
    if stem not in STEMS:
        raise SystemExit(f"kilix split: unknown stem {stem}")
    python = require_demucs()
    audio = absolute(audio)
    output = absolute(output)
    if not audio.is_file():
        raise SystemExit(f"kilix split: audio not found: {audio}")
    cache = demucs_cache()
    scratch = cache / "scratch"
    try:
        discard(scratch)
        scratch.mkdir(mode=0o700)
        scratch.chmod(0o700)
        arguments = [
            str(python),
            "-m",
            "demucs.separate",
            "--name",
            model,
            "--out",
            str(scratch),
            "--filename",
            "{stem}.{ext}",
            "--jobs",
            "1",
        ]
        if device != "auto":
            arguments.extend(("--device", device))
        arguments.append(str(audio))
        try:
            result = subprocess.run(
                arguments,
                check=False,
                capture_output=True,
                text=True,
                errors="replace",
                env=child_environment(cache, allow_downloads),
                stdin=subprocess.DEVNULL,
                timeout=90 * 60,
            )
        except subprocess.TimeoutExpired:
            raise SystemExit("kilix split: Demucs timed out") from None
        except OSError as error:
            raise SystemExit(f"kilix split: {error}") from error
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "Demucs failed").strip().splitlines()
            raise SystemExit("kilix split: " + (detail[-1] if detail else "Demucs failed"))
        try:
            deliver(read_stems(scratch, model), output, stem)
        except OSError as error:
            raise SystemExit(f"kilix split: {error}") from error
        return 0
    finally:
        discard(scratch)


def build_parser() -> argparse.ArgumentParser:
    parser = SplitParser(
        prog="kilix split",
        description=(
            "Separate a mix into stems with Demucs. "
            "The default stem is vocals. "
            "Install the pinned separator with: kilix split --install"
        ),
    )
    parser.add_argument(
        "audio", nargs="?", type=Path,
        help="audio file to separate")
    parser.add_argument(
        "--install", action="store_true",
        help="install the pinned Demucs into the private split venv and exit")
    parser.add_argument(
        "--output", type=Path,
        help="wav file for the requested stem, or a directory for every stem")
    parser.add_argument(
        "--stem", default="vocals",
        help="stem written to a .wav output (default: vocals). "
             "Stems: vocals, drums, bass, guitar, piano, other")
    parser.add_argument(
        "--model", default=MODEL,
        help="Demucs model (only htdemucs_6s)")
    parser.add_argument(
        "--device", default="auto",
        help="auto, cpu, or cuda (default: auto)")
    parser.add_argument(
        "--allow-model-downloads", action="store_true",
        help="allow Demucs to download model weights")
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        args = build_parser().parse_args(argv)
    except SystemExit as error:
        if error.code is None:
            return 0
        if isinstance(error.code, int):
            return error.code
        raise
    if args.install:
        return command_install()
    if args.model != MODEL:
        raise SystemExit(f"kilix split: unknown model {args.model}")
    if args.device not in DEVICES:
        raise SystemExit(f"kilix split: unknown device {args.device}")
    if args.stem not in STEMS:
        raise SystemExit(f"kilix split: unknown stem {args.stem}")
    if args.audio is None:
        raise SystemExit("kilix split: an audio file is required")
    if args.output is None:
        raise SystemExit("kilix split: --output is required")
    return command_split(
        args.audio, args.output, args.stem, args.model, args.device,
        args.allow_model_downloads)
