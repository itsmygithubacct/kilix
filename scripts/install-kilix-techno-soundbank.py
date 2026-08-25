#!/usr/bin/env python3
"""Install one pinned Kilix Techno soundbank subset without ambient tools."""
from __future__ import annotations

import argparse
import array
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import wave
import zipfile


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "config"))
import techno_soundbanks as catalog  # noqa: E402


MAX_SAMPLE_BYTES = 64 * 1024 * 1024
MAX_SAMPLE_SECONDS = 60
CANONICAL_RATE = 44_100
USER_AGENT = "kilix-techno-soundbank-installer/1"


class InstallError(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _download(url: str, expected_size: int, expected_sha256: str,
              destination: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    digest = hashlib.sha256()
    total = 0
    try:
        with urllib.request.urlopen(request, timeout=45) as response, \
                destination.open("xb") as output:
            while True:
                block = response.read(1024 * 1024)
                if not block:
                    break
                total += len(block)
                if total > expected_size:
                    raise InstallError(f"download exceeded pinned size: {url}")
                output.write(block)
                digest.update(block)
            output.flush()
            os.fsync(output.fileno())
    except OSError as error:
        raise InstallError(f"download failed: {url}: {error}") from error
    if total != expected_size:
        raise InstallError(
            f"download size changed for {url}: {total}, expected {expected_size}")
    actual = digest.hexdigest()
    if actual != expected_sha256:
        raise InstallError(
            f"download checksum changed for {url}: {actual}")


def _decode_pcm(raw: bytes, width: int, channels: int) -> list[int]:
    stride = width * channels
    if stride <= 0 or len(raw) % stride:
        raise InstallError("WAV PCM body is not frame-aligned")
    mono: list[int] = []
    for offset in range(0, len(raw), stride):
        total = 0
        for channel in range(channels):
            start = offset + channel * width
            sample = raw[start:start + width]
            if width == 1:
                value = (sample[0] - 128) << 8
            elif width == 2:
                value = int.from_bytes(sample, "little", signed=True)
            elif width == 3:
                value = int.from_bytes(sample, "little", signed=True) >> 8
            elif width == 4:
                value = int.from_bytes(sample, "little", signed=True) >> 16
            else:
                raise InstallError(f"unsupported PCM width: {width * 8} bits")
            total += value
        mono.append(max(-32768, min(32767, round(total / channels))))
    return mono


def _resample(samples: list[int], source_rate: int) -> list[int]:
    if source_rate == CANONICAL_RATE:
        return samples
    count = (len(samples) * CANONICAL_RATE + source_rate // 2) // source_rate
    output = [0] * count
    for index in range(count):
        position = index * source_rate
        source = position // CANONICAL_RATE
        fraction = position % CANONICAL_RATE
        if source >= len(samples) - 1:
            output[index] = samples[-1]
        else:
            left = samples[source]
            right = samples[source + 1]
            output[index] = round(
                (left * (CANONICAL_RATE - fraction) + right * fraction) /
                CANONICAL_RATE)
    return output


def _normalize_wav(source: Path, destination: Path) -> None:
    try:
        if source.stat().st_size > MAX_SAMPLE_BYTES:
            raise InstallError(f"sample exceeds {MAX_SAMPLE_BYTES} bytes")
        with wave.open(str(source), "rb") as reader:
            channels = reader.getnchannels()
            width = reader.getsampwidth()
            rate = reader.getframerate()
            frames = reader.getnframes()
            if reader.getcomptype() != "NONE" or not 1 <= channels <= 8 or \
                    width not in (1, 2, 3, 4) or not 8_000 <= rate <= 192_000 or \
                    frames == 0 or frames > rate * MAX_SAMPLE_SECONDS:
                raise InstallError(f"unsupported or unbounded WAV: {source.name}")
            raw = reader.readframes(frames)
            if len(raw) != frames * channels * width:
                raise InstallError(f"truncated WAV: {source.name}")
    except (OSError, wave.Error) as error:
        raise InstallError(f"cannot decode {source.name}: {error}") from error
    samples = _resample(_decode_pcm(raw, width, channels), rate)
    payload = array.array("h", samples)
    if sys.byteorder != "little":
        payload.byteswap()
    try:
        with wave.open(str(destination), "wb") as writer:
            writer.setnchannels(1)
            writer.setsampwidth(2)
            writer.setframerate(CANONICAL_RATE)
            writer.writeframes(payload.tobytes())
        os.chmod(destination, 0o644)
    except (OSError, wave.Error) as error:
        raise InstallError(f"cannot write {destination.name}: {error}") from error


def _direct(pack: dict, work: Path, stage: Path) -> None:
    prefix = pack.get("prefix", "")
    for index, (output, upstream, size, digest) in enumerate(pack["files"]):
        source = work / f"source-{index}.wav"
        _download(pack["raw_base"] + prefix + upstream, size, digest, source)
        _normalize_wav(source, stage / output)


def _archive(pack: dict, work: Path) -> Path:
    archive = work / ("source.zip" if pack["mode"] == "zip" else "source.tgz")
    _download(pack["archive_url"], pack["download_bytes"],
              pack["archive_sha256"], archive)
    return archive


def _zip(pack: dict, work: Path, stage: Path) -> None:
    archive = _archive(pack, work)
    try:
        with zipfile.ZipFile(archive) as source:
            for index, (output, member_name) in enumerate(pack["files"]):
                info = source.getinfo(member_name)
                if info.file_size <= 0 or info.file_size > MAX_SAMPLE_BYTES:
                    raise InstallError(f"unbounded ZIP member: {member_name}")
                extracted = work / f"source-{index}.wav"
                with source.open(info) as reader, extracted.open("xb") as writer:
                    shutil.copyfileobj(reader, writer, 1024 * 1024)
                if extracted.stat().st_size != info.file_size:
                    raise InstallError(f"truncated ZIP member: {member_name}")
                _normalize_wav(extracted, stage / output)
    except (KeyError, OSError, zipfile.BadZipFile) as error:
        raise InstallError(f"invalid pinned ZIP: {error}") from error


def _extract_forge(archive: Path, destination: Path) -> None:
    try:
        with tarfile.open(archive, "r:gz") as source:
            for member in source.getmembers():
                parts = PurePosixPath(member.name).parts
                if len(parts) < 2:
                    continue
                relative = parts[1:]
                if any(part in ("", ".", "..") for part in relative):
                    raise InstallError("unsafe path in forge archive")
                target = destination.joinpath(*relative)
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                if not member.isfile() or member.size > 2 * 1024 * 1024:
                    raise InstallError("unsupported member in forge archive")
                target.parent.mkdir(parents=True, exist_ok=True)
                reader = source.extractfile(member)
                if reader is None:
                    raise InstallError("unreadable member in forge archive")
                with reader, target.open("xb") as writer:
                    shutil.copyfileobj(reader, writer, 1024 * 1024)
                if target.stat().st_size != member.size:
                    raise InstallError("truncated member in forge archive")
    except (OSError, tarfile.TarError) as error:
        raise InstallError(f"invalid forge archive: {error}") from error


def _forge(pack: dict, work: Path, stage: Path) -> None:
    archive = _archive(pack, work)
    source = work / "forge-source"
    source.mkdir()
    _extract_forge(archive, source)
    built = work / "forge-output"
    built.mkdir()
    recipes = dict.fromkeys(item[1] for item in pack["outputs"])
    environment = dict(os.environ)
    environment["PYTHONHASHSEED"] = "0"
    for recipe in recipes:
        result = subprocess.run(
            [sys.executable, "-m", "forge", "build", recipe, "-o", str(built)],
            cwd=source, env=environment, capture_output=True, text=True,
            timeout=180, check=False)
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            raise InstallError(f"scoredata-forge failed: {detail[:300]}")
    for output, _recipe, generated in pack["outputs"]:
        sample = built / generated
        if not sample.is_file() or sample.is_symlink():
            raise InstallError(f"scoredata-forge did not emit {generated}")
        _normalize_wav(sample, stage / output)


def _write_metadata(pack: dict, stage: Path) -> None:
    files = {}
    sample_bytes = 0
    for name in catalog.output_names(pack):
        sample = stage / name
        files[name] = _sha256(sample)
        sample_bytes += sample.stat().st_size
    if sample_bytes != pack["installed_bytes"]:
        raise InstallError(
            f"normalized footprint changed: {sample_bytes}, expected "
            f"{pack['installed_bytes']}")
    notice = (
        f"{pack['label']} curated sample subset\n\n"
        f"Sample license: {pack['license']}\n"
        "CC0 1.0 Universal: https://creativecommons.org/publicdomain/zero/1.0/\n"
        f"Upstream license statement and source: {pack['source']}\n\n"
        "These samples are optional data. They are not relicensed under the "
        "Kilix Techno application's MIT code license.\n"
    )
    source_text = (
        f"Source: {pack['source']}\n"
        f"Pinned revision/release: {pack['revision']}\n"
        f"Downloaded bytes: {pack['download_bytes']}\n"
        f"Installed canonical sample bytes: {sample_bytes}\n"
        "Conversion: selected sources only; mono PCM, 16-bit, 44100 Hz.\n"
    )
    provenance = {
        "schema": 1,
        "id": pack["id"],
        "source": pack["source"],
        "revision": pack["revision"],
        "license": pack["license"],
        "download_bytes": pack["download_bytes"],
        "installed_sample_bytes": sample_bytes,
        "files": files,
    }
    for path, text in (
            (stage / "LICENSE.txt", notice),
            (stage / "SOURCE.txt", source_text),
            (stage / "PROVENANCE.json",
             json.dumps(provenance, indent=2, sort_keys=True) + "\n")):
        path.write_text(text, encoding="utf-8")
        os.chmod(path, 0o644)
    for path in stage.iterdir():
        if path.is_file() and not path.is_symlink():
            with path.open("rb") as handle:
                os.fsync(handle.fileno())
    receipt = stage / ".kilix-bank"
    receipt.write_text(json.dumps(provenance, sort_keys=True) + "\n",
                       encoding="utf-8")
    os.chmod(receipt, 0o644)
    with receipt.open("rb") as handle:
        os.fsync(handle.fileno())
    directory_fd = os.open(stage, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def install(pack: dict, assume_yes: bool) -> int:
    target = catalog.directory(pack)
    print(f"{pack['label']}")
    print(f"  download: {catalog.human_size(pack['download_bytes'])}")
    print(f"  installed samples: {catalog.human_size(pack['installed_bytes'])}")
    print(f"  license: {pack['license']}")
    print(f"  source: {pack['source']} @ {pack['revision']}")
    print(f"  destination: {target}")
    if catalog.ready(pack):
        print("already installed and receipt-verified")
        return 0
    if target.exists() or target.is_symlink():
        raise InstallError(
            f"refusing unmanaged or partial destination: {target}")
    if not assume_yes:
        try:
            answer = input("download and install? [y/N] ").strip().lower()
        except EOFError:
            answer = ""
        if answer not in ("y", "yes"):
            print("cancelled.")
            return 1
    root = catalog.root()
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(root, 0o700)
    with tempfile.TemporaryDirectory(prefix=f".{pack['directory']}.work-",
                                     dir=root) as temporary:
        work = Path(temporary)
        stage = work / "installed"
        stage.mkdir(mode=0o755)
        if pack["mode"] == "direct":
            _direct(pack, work, stage)
        elif pack["mode"] == "zip":
            _zip(pack, work, stage)
        elif pack["mode"] == "forge":
            _forge(pack, work, stage)
        else:
            raise InstallError(f"unknown install mode: {pack['mode']}")
        _write_metadata(pack, stage)
        os.rename(stage, target)
        directory_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    if not catalog.ready(pack):
        raise InstallError("published bank did not pass its receipt check")
    print(f"installed {pack['label']} ({catalog.human_size(pack['installed_bytes'])} samples)")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Install pinned optional sample subsets for Kilix Techno")
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--install", metavar="ID")
    action.add_argument("--check", metavar="ID")
    action.add_argument("--list", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--yes", "-y", action="store_true")
    args = parser.parse_args(argv)
    if args.list or (not args.install and not args.check):
        rows = catalog.rows()
        if args.json:
            print(json.dumps(rows, indent=2))
        else:
            for row in rows:
                state = "installed" if row["installed"] else row["size"]
                print(f"{row['id']:<28} {state}")
        return 0
    identifier = args.install or args.check
    pack = catalog.by_id(identifier)
    if pack is None:
        print(f"soundbank installer: unknown pack: {identifier}", file=sys.stderr)
        return 2
    if args.check:
        return 0 if catalog.ready(pack) else 1
    try:
        return install(pack, args.yes)
    except (InstallError, subprocess.TimeoutExpired) as error:
        print(f"soundbank installer: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
