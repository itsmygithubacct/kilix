"""Private Demucs splitter used by kilix split."""
import io
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "config"))
from kilix_sdk import split_cli as cli


def write_venv(data: Path, demucs: bool) -> None:
    bindir = data / "split" / "venv" / "bin"
    bindir.mkdir(parents=True)
    python = bindir / "python"
    python.write_text("#!/bin/sh\n", encoding="utf-8")
    python.chmod(0o700)
    if not demucs:
        return
    package = data / "split" / "venv" / "lib" / "python3.13" / "site-packages" / "demucs"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")


def fake_demucs(stems: tuple[str, ...]):
    calls = []

    def run(args, **kwargs):
        argv = list(args)
        calls.append((argv, kwargs.get("env") or {}))
        out = Path(argv[argv.index("--out") + 1])
        name = argv[argv.index("--name") + 1]
        folder = out / name.replace("hf://", "").replace("/", "_")
        folder.mkdir(parents=True, exist_ok=True)
        for stem in stems:
            (folder / f"{stem}.wav").write_bytes(stem.encode())
        return subprocess.CompletedProcess(argv, 0, "", "")

    return calls, run


class SplitCliTests(unittest.TestCase):
    def test_help_mentions_install_and_vocals(self):
        with patch("sys.stdout", new_callable=io.StringIO) as out:
            code = cli.main(["--help"])
        self.assertEqual(code, 0)
        text = out.getvalue()
        self.assertIn("--install", text)
        self.assertIn("vocals", text)

    def test_unknown_model_raises(self):
        with self.assertRaises(SystemExit) as caught:
            cli.main(["song.wav", "--output", "out.wav", "--model", "mdx"])
        self.assertTrue(str(caught.exception).startswith("kilix split:"))
        self.assertIn("mdx", str(caught.exception))

    def test_missing_venv_names_install(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            audio = root / "song.wav"
            audio.write_bytes(b"audio")
            environment = {"KILIX_DATA_HOME": str(root / "data")}
            with patch.dict(os.environ, environment, clear=False):
                with self.assertRaises(SystemExit) as caught:
                    cli.main([str(audio), "--stem", "vocals", "--output", str(root / "vocals.wav")])
        self.assertEqual(str(caught.exception), cli.NOT_INSTALLED)
        self.assertIn("kilix split --install", str(caught.exception))

    def test_venv_without_demucs_names_install(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = root / "data"
            write_venv(data, demucs=False)
            audio = root / "song.wav"
            audio.write_bytes(b"audio")
            with patch.dict(os.environ, {"KILIX_DATA_HOME": str(data)}, clear=False):
                with self.assertRaises(SystemExit) as caught:
                    cli.main([str(audio), "--output", str(root / "vocals.wav")])
        self.assertEqual(str(caught.exception), cli.NOT_INSTALLED)

    def test_fake_demucs_copies_the_vocal_stem(self):
        calls, run = fake_demucs(("vocals", "drums"))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = root / "data"
            write_venv(data, demucs=True)
            audio = root / "song.wav"
            audio.write_bytes(b"audio")
            output = root / "vocals.wav"
            environment = {"KILIX_DATA_HOME": str(data)}
            with patch.dict(os.environ, environment, clear=False), \
                 patch.object(cli.subprocess, "run", side_effect=run):
                code = cli.main([
                    str(audio), "--output", str(output), "--stem", "vocals",
                    "--model", "htdemucs_6s", "--device", "auto"])
            self.assertEqual(code, 0)
            argv, env = calls[0]
            self.assertEqual(argv[1:3], ["-m", "demucs.separate"])
            self.assertTrue(argv[0].endswith("/split/venv/bin/python"))
            self.assertEqual(argv[argv.index("--name") + 1], "htdemucs_6s")
            self.assertEqual(argv[argv.index("--filename") + 1], "{stem}.{ext}")
            self.assertEqual(argv[argv.index("--jobs") + 1], "1")
            self.assertNotIn("--device", argv)
            self.assertEqual(argv[-1], os.path.abspath(audio))
            scratch = Path(argv[argv.index("--out") + 1])
            self.assertFalse(scratch.exists())
            self.assertEqual(output.read_bytes(), b"vocals")
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)
            self.assertFalse((root / "drums.wav").exists())
            cache = data / "split" / "demucs"
            self.assertEqual(stat.S_IMODE(cache.stat().st_mode), 0o700)
            self.assertEqual(env["TORCH_HOME"], str(cache / "torch"))
            self.assertEqual(env["HF_HOME"], str(cache / "huggingface"))
            self.assertEqual(env["HF_HUB_DISABLE_TELEMETRY"], "1")
            self.assertEqual(env["HF_HUB_OFFLINE"], "1")
            self.assertEqual(env["http_proxy"], "http://127.0.0.1:9")
            self.assertEqual(env["https_proxy"], "http://127.0.0.1:9")
            self.assertEqual(env["HTTP_PROXY"], "http://127.0.0.1:9")
            self.assertEqual(env["HTTPS_PROXY"], "http://127.0.0.1:9")
            self.assertEqual(env["ALL_PROXY"], "http://127.0.0.1:9")
            self.assertEqual(env["NO_PROXY"], "")

    def test_device_cpu_is_passed_and_auto_is_not(self):
        calls, run = fake_demucs(("vocals",))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = root / "data"
            write_venv(data, demucs=True)
            audio = root / "song.wav"
            audio.write_bytes(b"audio")
            environment = {"KILIX_DATA_HOME": str(data)}
            with patch.dict(os.environ, environment, clear=False), \
                 patch.object(cli.subprocess, "run", side_effect=run):
                auto = cli.main([
                    str(audio), "--output", str(root / "auto.wav"), "--device", "auto"])
                cpu = cli.main([
                    str(audio), "--output", str(root / "cpu.wav"), "--device", "cpu",
                    "--allow-model-downloads"])
        self.assertEqual(auto, 0)
        self.assertEqual(cpu, 0)
        auto_argv, auto_env = calls[0]
        cpu_argv, cpu_env = calls[1]
        self.assertNotIn("--device", auto_argv)
        self.assertEqual(cpu_argv[cpu_argv.index("--device") + 1], "cpu")
        self.assertEqual(auto_env["HF_HUB_OFFLINE"], "1")
        self.assertNotIn("HF_HUB_OFFLINE", cpu_env)
        for key in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
            self.assertEqual(cpu_env.get(key), os.environ.get(key))

    def test_directory_output_keeps_every_stem(self):
        calls, run = fake_demucs(("vocals", "drums", "bass"))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = root / "data"
            write_venv(data, demucs=True)
            audio = root / "song.wav"
            audio.write_bytes(b"audio")
            output = root / "stems"
            with patch.dict(os.environ, {"KILIX_DATA_HOME": str(data)}, clear=False), \
                 patch.object(cli.subprocess, "run", side_effect=run):
                code = cli.main([str(audio), "--output", str(output)])
            self.assertEqual(code, 0)
            self.assertEqual((output / "vocals.wav").read_bytes(), b"vocals")
            self.assertEqual((output / "drums.wav").read_bytes(), b"drums")
            self.assertEqual((output / "bass.wav").read_bytes(), b"bass")
            for name in ("vocals", "drums", "bass"):
                mode = stat.S_IMODE((output / f"{name}.wav").stat().st_mode)
                self.assertEqual(mode, 0o600)
            scratch = Path(calls[0][0][calls[0][0].index("--out") + 1])
            self.assertFalse(scratch.exists())

    def test_failed_demucs_removes_scratch(self):
        def run(args, **kwargs):
            argv = list(args)
            run.scratch = Path(argv[argv.index("--out") + 1])
            self.assertTrue(run.scratch.is_dir())
            return subprocess.CompletedProcess(argv, 1, "", "demucs exploded")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = root / "data"
            write_venv(data, demucs=True)
            audio = root / "song.wav"
            audio.write_bytes(b"audio")
            with patch.dict(os.environ, {"KILIX_DATA_HOME": str(data)}, clear=False), \
                 patch.object(cli.subprocess, "run", side_effect=run):
                with self.assertRaises(SystemExit) as caught:
                    cli.main([str(audio), "--output", str(root / "vocals.wav")])
        self.assertIn("demucs exploded", str(caught.exception))
        self.assertTrue(str(caught.exception).startswith("kilix split:"))
        self.assertFalse(run.scratch.exists())


if __name__ == "__main__":
    os.chdir(ROOT)
    unittest.main()
