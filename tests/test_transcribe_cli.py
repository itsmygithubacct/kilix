"""The file-transcription front stays beside the prepared tiny-model route."""
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import wave
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "config"))
from kilix_sdk import transcribe_cli as cli
from kilix_sdk import transcribe_front as front


class RouteTests(unittest.TestCase):
    def test_help_keeps_the_prepared_runtime(self):
        result = subprocess.run(
            [str(ROOT / "kilix"), "transcribe", "--help"],
            capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("prepare", result.stdout)
        self.assertIn("serve", result.stdout)
        self.assertIn("engines", result.stdout)
        self.assertIn("install", result.stdout)
        self.assertIn("chunk", result.stdout)
        self.assertIn("probe", result.stdout)
        self.assertIn("--passes", result.stdout)
        self.assertIn("--clean", result.stdout)

    def test_prepare_stays_on_the_provider(self):
        with patch("kilix_sdk.transcribe_provider.main", return_value=7) as provider:
            self.assertEqual(front.main(["prepare", "--offline"]), 7)
        provider.assert_called_once_with(["prepare", "--offline"])

    def test_file_without_an_engine_stays_on_the_provider(self):
        with patch("kilix_sdk.transcribe_provider.main", return_value=0) as provider:
            self.assertEqual(front.main(["file", "speech.wav"]), 0)
        provider.assert_called_once_with(["file", "speech.wav"])

    def test_file_with_an_engine_uses_the_cli(self):
        with patch("kilix_sdk.transcribe_cli.main", return_value=0) as command, \
             patch("kilix_sdk.transcribe_provider.main") as provider:
            self.assertEqual(front.main(["file", "speech.wav", "--engine", "whistle"]), 0)
        provider.assert_not_called()
        command.assert_called_once_with(["file", "speech.wav", "--engine", "whistle"])

    def test_comma_separated_engines_use_the_cli(self):
        with patch("kilix_sdk.transcribe_cli.main", return_value=0) as command, \
             patch("kilix_sdk.transcribe_provider.main") as provider:
            self.assertEqual(front.main(["file", "speech.wav", "--engines", "whistle,small-en-us"]), 0)
        provider.assert_not_called()
        command.assert_called_once()


class ScoreTests(unittest.TestCase):
    def test_best_pass_is_the_fastest(self):
        chosen = cli.best_pass([
            {"pass": 1, "seconds": 3.0},
            {"pass": 2, "seconds": 1.5},
            {"pass": 3, "seconds": 2.0},
        ])
        self.assertEqual(chosen["pass"], 2)

    def test_word_error_rate_counts_a_substitution(self):
        self.assertEqual(cli.word_error_rate("the egg", "the eggs"), 0.5)
        self.assertEqual(cli.word_error_rate("The Egg.", "the egg"), 0.0)

    def test_chunk_length_stays_under_the_ten_minute_buffer(self):
        with self.assertRaises(SystemExit):
            cli.chunk_seconds(600)

    def test_fastest_pass_keeps_its_own_error_rate(self):
        result = cli.engine_result({"id": "whistle"}, [
            {"pass": 1, "seconds": 3.0, "rtf": 1.0, "text": "slow", "wer": 0.1, "segments": []},
            {"pass": 2, "seconds": 1.0, "rtf": 0.5, "text": "fast", "wer": 0.4, "segments": []},
        ], 0.2)
        self.assertEqual(result["best_pass"], 2)
        self.assertEqual(result["wer"], 0.4)
        self.assertEqual(result["best_wer"], 0.1)
        self.assertEqual(result["text"], "fast")

    def test_selection_merges_flags_and_rejects_duplicates(self):
        self.assertEqual(
            cli.selected_engines(["whistle"], "small-en-us, whisper-small-en"),
            ["whistle", "small-en-us", "whisper-small-en"])
        with self.assertRaises(SystemExit):
            cli.selected_engines(["whistle"], "whistle")
        with self.assertRaises(SystemExit):
            cli.selected_engines([], "")

    def test_default_install_is_one_engine(self):
        with self.assertRaises(SystemExit) as caught:
            cli.command_install(["whistle", "small-en-us"], True)
        self.assertIn("one engine", str(caught.exception))

    def test_captions_time_chunks_and_skip_silence(self):
        document = {"results": [{"segments": [
            {"start": 0.0, "end": 1.5, "text": "hello"},
            {"start": 1.5, "end": 2.0, "text": ""},
            {"start": 2.0, "end": 3.25, "text": "there"},
        ]}]}
        text = cli.render_captions(document, vtt=False)
        self.assertEqual(text.count("-->"), 2)
        self.assertIn("00:00:00,000 --> 00:00:01,500", text)
        self.assertIn("00:00:02,000 --> 00:00:03,250", text)
        self.assertTrue(cli.render_captions(document, vtt=True).startswith("WEBVTT\n"))

    def test_dictation_config_reaches_the_voice_library_lookup(self):
        def cfg_get(cfg, path):
            current = cfg
            for part in path.split("."):
                if not isinstance(current, dict) or part not in current:
                    return None
                current = current[part]
            return current

        config = cli.dictation_config({"id": "lgraph-en-us", "engine": "vosk"}, 4)
        self.assertEqual(cfg_get(config, "stt.engine"), "vosk")
        self.assertEqual(cfg_get(config, "stt.model"), "lgraph-en-us")
        self.assertEqual(cfg_get(config, "stt.threads"), 4)
        self.assertEqual(cfg_get(config, "audio.rate"), 16000)
        whistle = cli.dictation_config({"id": "whistle", "engine": "whistle"}, None)
        self.assertEqual(cfg_get(whistle, "stt.engine"), "whistle")
        self.assertIsNone(cfg_get(whistle, "stt.threads"))
        self.assertIsNone(cfg_get(whistle, "stt.model_path"))
        medium = cli.BY_ID["whisper-medium-en"]
        self.assertEqual(medium["engine"], "whisper")
        opened = cli.dictation_config(medium, None)
        self.assertEqual(cfg_get(opened, "stt.engine"), "whisper")
        self.assertEqual(cfg_get(opened, "stt.model"), "whisper-medium-en")
        self.assertEqual(
            cfg_get(opened, "stt.model_path"),
            str(cli.data_home() / "desktop-apps/assets/faster-whisper-medium-en"))

    def test_several_engines_share_one_chunking(self):
        parts = [Path("chunk-0000.wav"), Path("chunk-0001.wav")]

        def recognise(spec, parts, durations, audio, passes, threads, reference_text):
            return {
                "engine": spec["id"], "load_seconds": 0.1, "passes": [],
                "best_pass": 1, "best_seconds": 2.0, "best_rtf": 0.1,
                "text": spec["id"], "segments": [],
            }

        with tempfile.TemporaryDirectory() as directory, \
             patch.object(cli, "assert_installed"), \
             patch.object(cli, "chunk_audio", return_value=parts) as chunk, \
             patch.object(cli, "wav_seconds", return_value=10.0), \
             patch.object(cli, "recognise_engine", side_effect=recognise) as rec, \
             patch("sys.stdout", new_callable=io.StringIO) as out:
            code = cli.command_run(
                Path("speech.wav"), ["whistle", "small-en-us"], 3, 480, "json",
                None, None, Path(directory), None, False)
        self.assertEqual(code, 0)
        chunk.assert_called_once()
        self.assertEqual(rec.call_count, 2)
        payload = json.loads(out.getvalue())
        self.assertEqual(payload["schema"], "kilix.transcribe.run/v1")
        self.assertEqual([item["engine"] for item in payload["results"]], ["whistle", "small-en-us"])
        self.assertEqual(payload["audio_seconds"], 20.0)

    def test_uninstalled_engine_is_refused_before_chunking(self):
        with patch.object(cli, "live_catalog", return_value={"whistle": {"installed": False}}), \
             patch.object(cli, "chunk_audio") as chunk:
            with self.assertRaises(SystemExit) as caught:
                cli.command_run(
                    Path("speech.wav"), ["whistle"], 1, 480, "text",
                    None, None, Path("/tmp"), None, False)
        self.assertIn("kilix transcribe install whistle", str(caught.exception))
        chunk.assert_not_called()

    def test_dry_run_plans_chunks_without_decoding(self):
        info = {
            "duration_seconds": 1000.0, "sample_rate": 16000, "channels": 1,
            "codec": "mp3", "path": "speech.wav", "schema": "kilix.transcribe.probe/v1",
        }
        with patch.object(cli, "probe_audio", return_value=info), \
             patch.object(cli, "chunk_audio") as chunk, \
             patch("sys.stdout", new_callable=io.StringIO) as out:
            code = cli.command_run(
                Path("speech.wav"), ["whistle", "small-en-us"], 3, 480, "json",
                None, None, None, None, True)
        self.assertEqual(code, 0)
        chunk.assert_not_called()
        payload = json.loads(out.getvalue())
        self.assertEqual(payload["schema"], "kilix.transcribe.plan/v1")
        self.assertEqual(payload["chunks"], 3)
        self.assertEqual(payload["passes"], 3)
        self.assertEqual(payload["engines"], ["whistle", "small-en-us"])

    def test_clean_dry_run_does_not_separate(self):
        info = {
            "duration_seconds": 12.0, "sample_rate": 44100, "channels": 2,
            "codec": "mp3", "path": "song.mp3", "schema": "kilix.transcribe.probe/v1",
        }
        with patch.object(cli, "probe_audio", return_value=info), \
             patch.object(cli, "extract_vocal") as extract, \
             patch.object(cli, "chunk_audio") as chunk, \
             patch("sys.stdout", new_callable=io.StringIO) as out:
            code = cli.command_run(
                Path("song.mp3"), ["whistle"], 1, 480, "json",
                None, None, None, None, True, True)
        self.assertEqual(code, 0)
        extract.assert_not_called()
        chunk.assert_not_called()
        self.assertEqual(json.loads(out.getvalue())["clean"], "vocals")

    def test_clean_transcribes_the_vocal_stem(self):
        vocal = Path("vocals.wav")
        parts = [Path("chunk-0000.wav")]
        recognised = {
            "engine": "whistle", "load_seconds": 0.0, "passes": [],
            "best_pass": 1, "best_seconds": 1.0, "best_rtf": 0.1,
            "text": "vocal", "segments": [],
        }
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(cli, "assert_installed"), \
             patch.object(cli, "extract_vocal", return_value=vocal) as extract, \
             patch.object(cli, "chunk_audio", return_value=parts) as chunk, \
             patch.object(cli, "wav_seconds", return_value=10.0), \
             patch.object(cli, "recognise_engine", return_value=recognised), \
             patch("sys.stdout", new_callable=io.StringIO):
            code = cli.command_run(
                Path("song.mp3"), ["whistle"], 1, 480, "text",
                None, None, Path(directory), None, False, True)
        self.assertEqual(code, 0)
        extract.assert_called_once()
        self.assertEqual(chunk.call_args.args[0], vocal)

    def test_captions_refuse_several_engines(self):
        with patch.object(cli, "chunk_audio") as chunk:
            with self.assertRaises(SystemExit):
                cli.command_run(
                    Path("speech.wav"), ["whistle", "small-en-us"], 1, 480, "srt",
                    None, None, None, None, False)
        chunk.assert_not_called()


class ChunkTests(unittest.TestCase):
    def test_chunk_splits_a_wav(self):
        ffmpeg = __import__("shutil").which("ffmpeg")
        if not ffmpeg:
            self.skipTest("ffmpeg is not installed")
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "tone.wav"
            subprocess.run(
                [ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                 "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
                 "-ac", "1", "-ar", "16000", str(source)],
                check=True)
            output = Path(directory) / "parts"
            parts = cli.chunk_audio(source, output, 1)
            self.assertGreaterEqual(len(parts), 2)
            self.assertTrue(all(part.suffix == ".wav" for part in parts))
            self.assertGreater(sum(cli.wav_seconds(part) for part in parts), 1.5)
            with wave.open(str(parts[0])) as handle:
                self.assertEqual(handle.getframerate(), 16000)
                self.assertEqual(handle.getnchannels(), 1)
            if __import__("shutil").which("ffprobe"):
                info = cli.probe_audio(source)
                self.assertGreater(info["duration_seconds"], 1.5)
                self.assertEqual(info["sample_rate"], 16000)
                self.assertEqual(info["channels"], 1)


if __name__ == "__main__":
    os.chdir(ROOT)
    unittest.main()
