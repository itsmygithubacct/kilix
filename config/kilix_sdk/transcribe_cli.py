"""File transcription for every Kilix speech engine.

``engines`` lists the catalog. ``install`` runs ``kilix stt --install`` for a
dictation model and ``kilix models install`` for whisper-tiny-ggml.
``probe`` reads duration. ``chunk`` writes 16 kHz mono pieces under the
recogniser's ten-minute buffer. ``run`` and ``file --engine`` share those
pieces across the selected engines and report the fastest pass.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import wave
from pathlib import Path

# Dictation engines refuse a buffer past ten minutes. Requests stop at nine.
MAX_CHUNK_SECONDS = 540
DEFAULT_CHUNK_SECONDS = 480
BUFFER_SECONDS = 590
FRAME_BYTES = 640

ENGINES = (
    {"id": "small-en-us", "engine": "vosk", "kind": "dictation",
     "summary": "Vosk small US English"},
    {"id": "lgraph-en-us", "engine": "vosk", "kind": "dictation",
     "summary": "Vosk larger US English graph"},
    {"id": "vibevoice-asr-bitnet", "engine": "vibevoice", "kind": "dictation",
     "summary": "VibeVoice ASR BitNet, whole turn at a time"},
    {"id": "whisper-small-en", "engine": "whisper", "kind": "dictation",
     "summary": "faster-whisper small English"},
    {"id": "whisper-medium-en", "engine": "whisper", "kind": "dictation",
     "summary": "faster-whisper medium English",
     "asset": "desktop-apps/assets/faster-whisper-medium-en"},
    {"id": "whistle", "engine": "whistle", "kind": "dictation",
     "summary": "Whistle compact English"},
    {"id": "whisper-tiny-ggml", "engine": "whisper-tiny", "kind": "provider",
     "summary": "whisper.cpp tiny, the prepared kilix transcribe runtime"},
)
BY_ID = {item["id"]: item for item in ENGINES}


def kilix_bin() -> str:
    home = os.environ.get("KILIX_HOME")
    if home:
        candidate = Path(home) / "kilix"
        if candidate.is_file():
            return str(candidate)
    found = shutil.which("kilix")
    if found:
        return found
    raise SystemExit("kilix transcribe: cannot find the kilix program")


def voice_library() -> Path:
    data = os.environ.get("KILIX_DATA_HOME")
    root = Path(data) if data else Path.home() / ".local/gpu_terminal/kilix/data"
    library = root / "voice/runtime/current/lib/kilix-voice"
    if not library.is_dir():
        raise SystemExit(
            "kilix transcribe: the voice runtime is not installed. "
            "Run: kilix voice install")
    return library


def live_catalog() -> dict:
    try:
        result = subprocess.run(
            [kilix_bin(), "stt", "--models", "--json"],
            check=False, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return {}
    if result.returncode != 0 or not result.stdout.strip():
        return {}
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return {}
    models = payload.get("models")
    if not isinstance(models, list):
        return {}
    return {item.get("id"): item for item in models if isinstance(item, dict)}


def install_via(spec: dict) -> list[str]:
    if spec["kind"] == "provider":
        return ["kilix", "models", "install", spec["id"]]
    return ["kilix", "stt", "--install", spec["id"]]


def engine_rows() -> list[dict]:
    live = live_catalog()
    rows = []
    for spec in ENGINES:
        current = live.get(spec["id"]) or {}
        row = {
            "id": spec["id"],
            "engine": spec["engine"],
            "kind": spec["kind"],
            "summary": current.get("summary") or spec["summary"],
            "installed": current.get("installed"),
            "runtime_supported": current.get("runtime_supported"),
            "selected": current.get("selected"),
            "install": ["kilix", "transcribe", "install", spec["id"]],
            "via": install_via(spec),
        }
        if current.get("download_bytes") is not None:
            row["bytes"] = current["download_bytes"]
        if current.get("path"):
            row["path"] = current["path"]
        rows.append(row)
    return rows


def require_engine(engine_id: str) -> dict:
    spec = BY_ID.get(engine_id)
    if spec is None:
        known = ", ".join(BY_ID)
        raise SystemExit(f"kilix transcribe: unknown engine {engine_id}. Known: {known}")
    return spec


def selected_engines(repeated: list[str], comma: str) -> list[str]:
    chosen = [item.strip() for item in repeated if item and item.strip()]
    if comma.strip():
        chosen.extend(item.strip() for item in comma.split(",") if item.strip())
    if not chosen:
        raise SystemExit(
            "kilix transcribe: select an engine with --engine ENGINE or --engines A,B")
    if len(chosen) != len(set(chosen)):
        raise SystemExit("kilix transcribe: an engine was selected more than once")
    return chosen


def chunk_seconds(value: float) -> float:
    if not 1 <= value <= MAX_CHUNK_SECONDS:
        raise SystemExit(
            f"kilix transcribe: chunk length must be from 1 to {MAX_CHUNK_SECONDS} seconds")
    return value


def chunk_count(duration: float, seconds: float) -> int:
    if duration <= 0 or seconds <= 0:
        raise SystemExit("kilix transcribe: the audio has no duration")
    return max(1, math.ceil(duration / seconds))


def run_ffmpeg(arguments: list[str]) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise SystemExit("kilix transcribe: ffmpeg is required to decode and chunk audio")
    result = subprocess.run([ffmpeg, *arguments], check=False, capture_output=True, text=True)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "ffmpeg failed").strip().splitlines()
        raise SystemExit("kilix transcribe: " + (detail[-1] if detail else "ffmpeg failed"))


def chunk_audio(source: Path, output: Path, seconds: float) -> list[Path]:
    if not source.is_file():
        raise SystemExit(f"kilix transcribe: audio not found: {source}")
    output.mkdir(parents=True, exist_ok=True)
    for old in output.glob("chunk-*.wav"):
        old.unlink()
    pattern = output / "chunk-%04d.wav"
    run_ffmpeg([
        "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(source),
        "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le",
        "-f", "segment", "-segment_time", str(seconds),
        str(pattern),
    ])
    parts = sorted(output.glob("chunk-*.wav"))
    if not parts:
        raise SystemExit("kilix transcribe: ffmpeg produced no chunks")
    return parts


def wav_seconds(path: Path) -> float:
    try:
        with wave.open(str(path)) as handle:
            rate = handle.getframerate()
            if rate <= 0:
                return 0.0
            return handle.getnframes() / rate
    except (wave.Error, EOFError) as error:
        raise SystemExit(f"kilix transcribe: {path} is not a wav file ({error})") from error


def read_pcm(path: Path) -> bytes:
    with wave.open(str(path)) as handle:
        if handle.getnchannels() != 1 or handle.getsampwidth() != 2 or handle.getframerate() != 16000:
            raise SystemExit(f"kilix transcribe: {path} is not 16 kHz mono 16-bit PCM")
        return handle.readframes(handle.getnframes())


def probe_audio(source: Path) -> dict:
    if not source.is_file():
        raise SystemExit(f"kilix transcribe: audio not found: {source}")
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise SystemExit("kilix transcribe: ffprobe is required to read audio")
    result = subprocess.run(
        [ffprobe, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(source)],
        check=False, capture_output=True, text=True)
    if result.returncode != 0:
        detail = (result.stderr or "ffprobe failed").strip().splitlines()
        raise SystemExit("kilix transcribe: " + (detail[-1] if detail else "ffprobe failed"))
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise SystemExit("kilix transcribe: ffprobe returned no audio description") from error
    streams = [item for item in payload.get("streams") or [] if isinstance(item, dict)]
    audio_stream = next((item for item in streams if item.get("codec_type") == "audio"), None)
    if audio_stream is None and streams:
        audio_stream = streams[0]
    if audio_stream is None:
        raise SystemExit("kilix transcribe: no audio stream")
    raw_duration = audio_stream.get("duration") or (payload.get("format") or {}).get("duration")
    try:
        seconds = float(raw_duration)
    except (TypeError, ValueError) as error:
        raise SystemExit("kilix transcribe: the audio has no duration") from error
    try:
        rate = int(audio_stream["sample_rate"]) if audio_stream.get("sample_rate") is not None else None
    except (TypeError, ValueError):
        rate = None
    channels = audio_stream.get("channels")
    return {
        "schema": "kilix.transcribe.probe/v1",
        "path": str(source),
        "duration_seconds": seconds,
        "sample_rate": rate,
        "channels": channels if isinstance(channels, int) else None,
        "codec": audio_stream.get("codec_name"),
    }


def words_of(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", text.lower())


def word_error_rate(reference: str, hypothesis: str) -> float:
    left = words_of(reference)
    right = words_of(hypothesis)
    if not left:
        return 0.0 if not right else 1.0
    previous = list(range(len(right) + 1))
    for i, token in enumerate(left, 1):
        current = [i]
        for j, other in enumerate(right, 1):
            cost = 0 if token == other else 1
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + cost))
        previous = current
    return previous[-1] / len(left)


def best_pass(passes: list[dict]) -> dict:
    if not passes:
        raise SystemExit("kilix transcribe: no timed pass was recorded")
    return min(passes, key=lambda item: (item["seconds"], item["pass"]))


def join_text(texts: list[str]) -> str:
    return " ".join(piece.strip() for piece in texts if piece and piece.strip())


def segment_rows(durations: list[float], texts: list[str]) -> list[dict]:
    clock = 0.0
    rows = []
    for duration, text in zip(durations, texts):
        rows.append({
            "start": round(clock, 3),
            "end": round(clock + duration, 3),
            "text": (text or "").strip(),
        })
        clock += duration
    return rows


def pass_record(number: int, elapsed: float, audio: float, texts: list[str],
                durations: list[float], reference_text: str | None) -> dict:
    text = join_text(texts)
    record = {
        "pass": number,
        "seconds": round(elapsed, 6),
        "rtf": round(elapsed / audio, 6),
        "text": text,
        "segments": segment_rows(durations, texts),
    }
    if reference_text is not None:
        record["wer"] = word_error_rate(reference_text, text)
    return record


def engine_result(spec: dict, timed: list[dict], load_seconds: float | None) -> dict:
    chosen = best_pass(timed)
    payload = {
        "engine": spec["id"],
        "load_seconds": None if load_seconds is None else round(load_seconds, 6),
        "passes": timed,
        "best_pass": chosen["pass"],
        "best_seconds": chosen["seconds"],
        "best_rtf": chosen["rtf"],
        "text": chosen["text"],
        "segments": chosen["segments"],
    }
    if "wer" in chosen:
        payload["wer"] = chosen["wer"]
        payload["best_wer"] = min(item["wer"] for item in timed)
    return payload


def import_make_stt():
    library = str(voice_library())
    if library not in sys.path:
        sys.path.insert(0, library)
    from voicelib.stt import SttError, make_stt
    return make_stt, SttError


def data_home() -> Path:
    raw = os.environ.get("KILIX_DATA_HOME")
    if raw:
        return Path(raw)
    return Path.home() / ".local/gpu_terminal/kilix/data"


def dictation_config(spec: dict, threads: int | None) -> dict:
    """Nested keys. The voice library looks up ``stt.engine`` inside ``stt``."""
    stt = {"engine": spec["engine"], "model": spec["id"]}
    # Whisper has one catalogue id. model_path is the directory it opens.
    asset = spec.get("asset")
    if asset:
        stt["model_path"] = str(data_home() / asset)
    if threads is not None:
        stt["threads"] = threads
    return {"stt": stt, "audio": {"rate": 16000}}


def open_dictation(spec: dict, threads: int | None):
    make_stt, stt_error = import_make_stt()
    config = dictation_config(spec, threads)
    started = time.perf_counter()
    try:
        recogniser = make_stt(config, 16000)
    except stt_error as error:
        raise SystemExit(f"kilix transcribe: {error}") from error
    load = time.perf_counter() - started
    if type(recogniser).__name__ == "NullStt":
        recogniser.close()
        raise SystemExit(f"kilix transcribe: {spec['id']} is not available in this voice runtime")
    return recogniser, stt_error, load


def feed_dictation(recogniser, stt_error, parts: list[Path], label: str) -> list[str]:
    texts: list[str] = []
    try:
        for index, part in enumerate(parts, 1):
            print(f"{label} chunk {index}/{len(parts)}", file=sys.stderr, flush=True)
            pcm = read_pcm(part)
            recogniser.start_utterance()
            for offset in range(0, len(pcm), FRAME_BYTES):
                recogniser.feed(pcm[offset:offset + FRAME_BYTES])
            texts.append(recogniser.end_utterance() or "")
    except stt_error as error:
        raise SystemExit(f"kilix transcribe: {error}") from error
    return texts


def transcribe_provider(parts: list[Path], label: str) -> list[str]:
    texts = []
    program = kilix_bin()
    for index, part in enumerate(parts, 1):
        print(f"{label} chunk {index}/{len(parts)}", file=sys.stderr, flush=True)
        result = subprocess.run(
            [program, "transcribe", "file", str(part), "--format", "json"],
            check=False, capture_output=True, text=True)
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "transcribe file failed").strip()
            raise SystemExit("kilix transcribe: " + detail.splitlines()[-1])
        raw = result.stdout.strip()
        text = raw
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, dict):
            for key in ("text", "transcript", "utterance"):
                if isinstance(payload.get(key), str):
                    text = payload[key]
                    break
            else:
                text = _nested_text(payload) or raw
        texts.append(text.strip())
    return texts


def _nested_text(payload: dict) -> str:
    for key in ("result", "results", "utterances"):
        value = payload.get(key)
        if isinstance(value, dict):
            found = _nested_text(value)
            if found:
                return found
        if isinstance(value, list):
            pieces = []
            for item in value:
                if isinstance(item, str):
                    pieces.append(item)
                elif isinstance(item, dict):
                    found = _nested_text(item) or item.get("text") or ""
                    if isinstance(found, str) and found.strip():
                        pieces.append(found.strip())
            if pieces:
                return " ".join(pieces)
    return ""


def recognise_engine(spec: dict, parts: list[Path], durations: list[float], audio: float,
                     passes: int, threads: int | None, reference_text: str | None) -> dict:
    closer = None
    load = None
    try:
        if spec["kind"] == "provider":
            def once(label: str) -> list[str]:
                return transcribe_provider(parts, label)
        else:
            recogniser, stt_error, load = open_dictation(spec, threads)
            closer = recogniser.close

            def once(label: str) -> list[str]:
                return feed_dictation(recogniser, stt_error, parts, label)

        timed = []
        for number in range(1, passes + 1):
            started = time.perf_counter()
            texts = once(f"{spec['id']} pass {number}/{passes}")
            elapsed = time.perf_counter() - started
            record = pass_record(number, elapsed, audio, texts, durations, reference_text)
            timed.append(record)
            line = f"{spec['id']} pass {number}/{passes}: {elapsed:.2f}s rtf={record['rtf']:.3f}"
            if "wer" in record:
                line += f" wer={record['wer']:.4f}"
            print(line, file=sys.stderr, flush=True)
        result = engine_result(spec, timed, load)
        summary = (
            f"{spec['id']} best pass {result['best_pass']}: "
            f"{result['best_seconds']:.2f}s rtf={result['best_rtf']:.3f}"
        )
        if "wer" in result:
            summary += f" wer={result['wer']:.4f} best_wer={result['best_wer']:.4f}"
        print(summary, file=sys.stderr, flush=True)
        return result
    finally:
        if closer is not None:
            closer()


def extract_vocal(source: Path, destination: Path) -> Path:
    """Ask ``kilix split`` for the vocal stem and return that wav."""
    if not source.is_file():
        raise SystemExit(f"kilix transcribe: audio not found: {source}")
    destination.mkdir(parents=True, exist_ok=True)
    vocal = destination / "vocals.wav"
    result = subprocess.run(
        [kilix_bin(), "split", str(source), "--stem", "vocals", "--output", str(vocal)],
        check=False)
    if result.returncode != 0 or not vocal.is_file():
        raise SystemExit(
            "kilix transcribe: vocal separation failed. "
            "Install the splitter with: kilix split --install")
    return vocal


def _first_use() -> bool:
    value = os.environ.get("KILIX_MODEL_AUTO_INSTALL")
    if value is not None and value.strip().casefold() in {"0", "no", "false", "off"}:
        return False
    try:
        return sys.stdin.isatty() and sys.stdout.isatty()
    except (AttributeError, OSError, ValueError):
        return False


def _missing_engines(specs: list[dict], live: dict) -> list[str]:
    missing = []
    for spec in specs:
        if spec["kind"] == "provider":
            continue
        if (live.get(spec["id"]) or {}).get("installed") is False:
            missing.append(spec["id"])
    return missing


def assert_installed(specs: list[dict]) -> None:
    providers = [spec for spec in specs if spec["kind"] == "provider"]
    if providers and _first_use():
        for spec in providers:
            subprocess.call([kilix_bin(), *install_via(spec)[1:]])
    needed = [spec for spec in specs if spec["kind"] != "provider"]
    if not needed:
        return
    live = live_catalog()
    if not live:
        return
    missing = _missing_engines(needed, live)
    if missing and _first_use():
        by_id = {spec["id"]: spec for spec in needed}
        for item in missing:
            subprocess.call([kilix_bin(), *install_via(by_id[item])[1:]])
        live = live_catalog()
        missing = _missing_engines(needed, live) if live else missing
    if not missing:
        return
    lines = "\n".join(f"kilix transcribe install {item}" for item in missing)
    raise SystemExit("kilix transcribe: not installed:\n" + lines)


def emit(body: str, output: Path | None) -> None:
    if not body.endswith("\n"):
        body += "\n"
    if output is None:
        sys.stdout.write(body)
        return
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(body, encoding="utf-8")


def mark(value) -> str:
    if value is None:
        return "unknown"
    return "yes" if value else "no"


def command_engines(json_output: bool, installed_only: bool) -> int:
    rows = engine_rows()
    if installed_only:
        rows = [row for row in rows if row["installed"] is True]
    if json_output:
        emit(json.dumps({"schema": "kilix.transcribe.engines/v1", "engines": rows}, indent=2), None)
        return 0
    for row in rows:
        print(
            f"{row['id']}\t{row['engine']}\tinstalled={mark(row['installed'])}\t"
            f"selected={mark(row['selected'])}\truntime={mark(row['runtime_supported'])}"
        )
        print(f"  {row['summary']}")
        print(f"  {' '.join(row['install'])}")
    return 0


def command_install(engine_ids: list[str], make_default: bool) -> int:
    if make_default and len(engine_ids) != 1:
        raise SystemExit("kilix transcribe: --default applies to one engine")
    for engine_id in engine_ids:
        spec = require_engine(engine_id)
        if make_default and spec["kind"] != "dictation":
            raise SystemExit("kilix transcribe: --default applies to a dictation engine")
        print(f"install {spec['id']}", file=sys.stderr, flush=True)
        if spec["kind"] == "provider":
            status = subprocess.call([kilix_bin(), "models", "install", spec["id"]])
            if status == 0:
                print(
                    "kilix transcribe: weights installed. Prepare the runtime with "
                    "kilix transcribe prepare --engine PATH --engine-sha256 SHA256 "
                    "--decoder PATH --decoder-sha256 SHA256",
                    file=sys.stderr)
            if status != 0:
                return status
            continue
        command = [kilix_bin(), "stt", "--install", spec["id"]]
        if make_default:
            command.extend(["--default", spec["id"]])
        status = subprocess.call(command)
        if status != 0:
            return status
    return 0


def command_probe(source: Path, json_output: bool) -> int:
    info = probe_audio(source)
    if json_output:
        emit(json.dumps(info, indent=2), None)
        return 0
    rate = info["sample_rate"] if info["sample_rate"] is not None else "?"
    channels = info["channels"] if info["channels"] is not None else "?"
    codec = info["codec"] or "?"
    print(f"{info['duration_seconds']:.2f}s\t{rate} Hz\t{channels} ch\t{codec}\t{info['path']}")
    return 0


def command_chunk(source: Path, output: Path, seconds: float, json_output: bool) -> int:
    parts = chunk_audio(source, output, chunk_seconds(seconds))
    manifest = [{"path": str(part), "seconds": wav_seconds(part)} for part in parts]
    if json_output:
        emit(json.dumps({
            "schema": "kilix.transcribe.chunks/v1",
            "sample_rate": 16000,
            "channels": 1,
            "seconds": sum(item["seconds"] for item in manifest),
            "chunks": manifest,
        }, indent=2), None)
        return 0
    for part in parts:
        print(part)
    return 0


def render_text(document: dict) -> str:
    results = document["results"]
    if len(results) == 1:
        return results[0]["text"]
    return "\n\n".join(f"=== {item['engine']} ===\n{item['text']}" for item in results)


def caption_clock(seconds: float, vtt: bool) -> str:
    whole = int(seconds)
    millis = int(round((seconds - whole) * 1000))
    if millis == 1000:
        whole += 1
        millis = 0
    hours, remainder = divmod(whole, 3600)
    minutes, secs = divmod(remainder, 60)
    mark = "." if vtt else ","
    return f"{hours:02d}:{minutes:02d}:{secs:02d}{mark}{millis:03d}"


def render_captions(document: dict, vtt: bool) -> str:
    blocks = []
    number = 1
    for segment in document["results"][0]["segments"]:
        text = segment["text"].strip()
        if not text:
            continue
        cue = f"{caption_clock(segment['start'], vtt)} --> {caption_clock(segment['end'], vtt)}\n{text}\n"
        if not vtt:
            cue = f"{number}\n{cue}"
        blocks.append(cue)
        number += 1
    body = "\n".join(blocks)
    if vtt:
        return "WEBVTT\n\n" + body
    return body


def render_document(document: dict, output_format: str) -> str:
    if output_format == "json":
        return json.dumps(document, indent=2, ensure_ascii=False)
    if output_format == "srt":
        return render_captions(document, vtt=False)
    if output_format == "vtt":
        return render_captions(document, vtt=True)
    return render_text(document)


def command_run(source: Path, engines: list[str], passes: int, seconds: float,
                output_format: str, reference: Path | None, threads: int | None,
                work: Path | None, output: Path | None, dry_run: bool,
                clean: bool = False) -> int:
    specs = [require_engine(engine_id) for engine_id in engines]
    if passes < 1:
        raise SystemExit("kilix transcribe: --passes must be at least 1")
    if output_format in {"srt", "vtt"} and len(specs) != 1:
        raise SystemExit("kilix transcribe: srt and vtt take one engine. Use --format json for several.")
    limit = chunk_seconds(seconds)
    reference_text = None
    if reference is not None:
        if not reference.is_file():
            raise SystemExit(f"kilix transcribe: reference not found: {reference}")
        reference_text = reference.read_text(encoding="utf-8")
    if dry_run:
        info = probe_audio(source)
        plan = {
            "schema": "kilix.transcribe.plan/v1",
            "audio_seconds": info["duration_seconds"],
            "chunk_seconds": limit,
            "chunks": chunk_count(info["duration_seconds"], limit),
            "passes": passes,
            "engines": [spec["id"] for spec in specs],
            "format": output_format,
        }
        if clean:
            plan["clean"] = "vocals"
        body = json.dumps(plan, indent=2) if output_format == "json" else (
            f"{', '.join(plan['engines'])}\n"
            f"audio {plan['audio_seconds']:.2f}s, {plan['chunks']} chunks of {limit:g}s, "
            f"{passes} passes"
        )
        emit(body, output)
        return 0
    assert_installed(specs)

    def go(destination: Path) -> int:
        audio_source = extract_vocal(source, destination / "stems") if clean else source
        parts = chunk_audio(audio_source, destination / "chunks", limit)
        durations = [wav_seconds(part) for part in parts]
        if any(item > BUFFER_SECONDS for item in durations):
            raise SystemExit(
                "kilix transcribe: a chunk is past the recogniser's ten-minute buffer. "
                "Use a shorter --chunk-seconds.")
        audio = sum(durations)
        if audio <= 0:
            raise SystemExit("kilix transcribe: the audio has no duration")
        document = {
            "schema": "kilix.transcribe.run/v1",
            "clock": "recognition",
            "audio_seconds": round(audio, 3),
            "chunks": len(parts),
            "chunk_seconds": limit,
            "results": [
                recognise_engine(spec, parts, durations, audio, passes, threads, reference_text)
                for spec in specs
            ],
        }
        emit(render_document(document, output_format), output)
        return 0

    if work is None:
        with tempfile.TemporaryDirectory(prefix="kilix-transcribe-") as directory:
            return go(Path(directory))
    return go(work)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="kilix transcribe", add_help=False)
    commands = parser.add_subparsers(dest="command", required=True)
    engines = commands.add_parser("engines")
    engines.add_argument("--json", action="store_true")
    engines.add_argument("--installed", action="store_true")
    install = commands.add_parser("install")
    install.add_argument("engine", nargs="+")
    install.add_argument("--default", action="store_true")
    probe = commands.add_parser("probe")
    probe.add_argument("audio", type=Path)
    probe.add_argument("--json", action="store_true")
    chunk = commands.add_parser("chunk")
    chunk.add_argument("audio", type=Path)
    chunk.add_argument("--output", type=Path, required=True)
    chunk.add_argument("--seconds", type=float, default=DEFAULT_CHUNK_SECONDS)
    chunk.add_argument("--json", action="store_true")
    for name in ("run", "file"):
        run = commands.add_parser(name)
        run.add_argument("audio", type=Path)
        run.add_argument("--engine", action="append", default=[], dest="engine_ids")
        run.add_argument("--engines", default="")
        run.add_argument("--passes", type=int, default=1)
        run.add_argument("--chunk-seconds", type=float, default=DEFAULT_CHUNK_SECONDS)
        run.add_argument("--format", choices=("text", "json", "srt", "vtt"), default="text")
        run.add_argument("--reference", type=Path)
        run.add_argument("--threads", type=int)
        run.add_argument("--work", type=Path)
        run.add_argument("--output", type=Path)
        run.add_argument("--clean", action="store_true")
        run.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        args = build_parser().parse_args(argv)
    except SystemExit as error:
        return int(error.code) if isinstance(error.code, int) else 1
    if args.command == "engines":
        return command_engines(args.json, args.installed)
    if args.command == "install":
        return command_install(args.engine, args.default)
    if args.command == "probe":
        return command_probe(args.audio, args.json)
    if args.command == "chunk":
        return command_chunk(args.audio, args.output, args.seconds, args.json)
    return command_run(
        args.audio, selected_engines(args.engine_ids, args.engines), args.passes,
        args.chunk_seconds, args.format, args.reference, args.threads, args.work,
        args.output, args.dry_run, args.clean)
