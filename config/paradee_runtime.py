"""Paradee system voice for a fresh install.

The supervisor stays on the system interpreter. Synthesis runs in the
short-path environment at /usr/lib/kilix/pd. A present model directory is
checked in place. When that directory is absent, a terminal first use runs
``kilix models ensure paradee-8m-v1.2``, which shows the licence screen.
The caller answers it. This module does not import huggingface_hub.
``KILIX_MODEL_AUTO_INSTALL=0`` and a non-terminal leave the directory
uninstalled. The short-path interpreter stays the image copy.
"""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

PARADEE_ID = 'paradee-8m-v1.2'
DEFAULT_VOICE = 'af_heart'
ONNX_NAME = 'onnx/paradee_int8.onnx'
ONNX_BYTES = 8462119
ONNX_SHA256 = 'a6989796692ac173597e15d405c971989202d5c73cfad65f22e90bd7b6c97da1'
CONFIG_BYTES = 1827
CONFIG_SHA256 = '002535cf8dd08a434cdddb13372da8a7b4844152bdf9b5b4e7342e12ec30bcfb'
LICENCE_NAME = 'notices/LICENSE-apache-2.0.txt'
LICENCE_BYTES = 11358
LICENCE_SHA256 = 'cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30'
READY = 'System voice ready: paradee af_heart'
GOODBYE = 'System voice goodbye complete'


def state_file():
    """Same path as config/model_wizard.py state_root(), without importing it."""
    base = Path(os.environ.get('GPU_TERMINAL_HOME', '~/.local/gpu_terminal')).expanduser()
    root = Path(os.environ.get('KILIX95_STORAGE_HOME', str(base / 'kilix-95'))).expanduser()
    state = Path(os.environ.get('KILIX95_STATE_HOME', str(root / 'state'))).expanduser()
    return state / 'system-voice-default.json'


def saved_selection():
    path = state_file()
    try:
        data = json_load(path)
    except (OSError, ValueError):
        return {}
    if data.get('schema') != 'kilix.system-voice-default/v1':
        return {}
    engine, voice = data.get('engine'), data.get('voice')
    if not isinstance(engine, str) or not isinstance(voice, str) or not engine or not voice:
        return {}
    return data


def json_load(path):
    import json
    raw = Path(path).read_bytes()
    if len(raw) > 1024 * 1024:
        raise ValueError('voice selection is too large')
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError('voice selection is not an object')
    return data


def _first_use():
    value = os.environ.get('KILIX_MODEL_AUTO_INSTALL')
    if value is not None and value.strip().casefold() in {'0', 'no', 'false', 'off'}:
        return False
    try:
        return sys.stdin.isatty() and sys.stdout.isatty()
    except (AttributeError, OSError, ValueError):
        return False


def _launcher():
    path = Path(__file__).resolve().parents[1] / 'kilix'
    if path.is_symlink() or not path.is_file():
        return None
    return path


def _configured_dir():
    return Path(os.environ.get('KILIX_PARADEE_MODEL', '/usr/lib/kilix/paradee'))


def _verified_store():
    """A receipt-backed catalog copy. Verify does not install."""
    launcher = _launcher()
    if launcher is None:
        return None
    try:
        result = subprocess.run(
            [str(launcher), 'models', 'verify', PARADEE_ID],
            capture_output=True, text=True, stdin=subprocess.DEVNULL, check=False)
    except OSError:
        return None
    if result.returncode != 0:
        return None
    try:
        report = json.loads(result.stdout)
    except ValueError:
        return None
    if not isinstance(report, dict):
        return None
    root = report.get('root')
    if report.get('verified') != PARADEE_ID or not isinstance(root, str) or not root.startswith('/'):
        return None
    candidate = Path(root) / 'assets' / PARADEE_ID
    if candidate.is_symlink() or not _ready(candidate):
        return None
    return candidate


def _ensure_store():
    """Show the licence screen. Nothing here answers it."""
    launcher = _launcher()
    if launcher is None:
        return False
    try:
        completed = subprocess.run(
            [str(launcher), 'models', 'ensure', PARADEE_ID], check=False)
    except OSError:
        return False
    if completed.returncode != 0:
        raise SystemExit(PARADEE_ID + ' was not installed; the licence was not accepted')
    return True


def model_dir():
    """Image directory when it exists. Otherwise the catalog copy, installed on a terminal."""
    configured = _configured_dir()
    if configured.is_symlink():
        raise SystemExit('Paradee model path is a symlink')
    if configured.is_dir():
        return configured
    stored = _verified_store()
    if stored is not None:
        return stored
    if _first_use() and _ensure_store():
        stored = _verified_store()
        if stored is not None:
            return stored
    return configured


def runtime_python():
    return Path(os.environ.get('KILIX_PARADEE_PYTHON', '/usr/lib/kilix/pd/bin/python'))


def _read_exact(path, size, digest, label):
    target = Path(path)
    if target.is_symlink() or not target.is_file():
        raise SystemExit('Paradee ' + label + ' is missing: ' + str(target))
    data = target.read_bytes()
    if len(data) != size or hashlib.sha256(data).hexdigest() != digest:
        raise SystemExit('Paradee ' + label + ' size or checksum mismatch')
    return target


def _checked(root):
    return (_read_exact(root / ONNX_NAME, ONNX_BYTES, ONNX_SHA256, 'weights'),
            _read_exact(root / 'config.json', CONFIG_BYTES, CONFIG_SHA256, 'config'),
            _read_exact(root / LICENCE_NAME, LICENCE_BYTES, LICENCE_SHA256, 'licence'))


def _ready(root):
    try:
        _checked(root)
    except SystemExit:
        return False
    return True


def model_files():
    return _checked(model_dir())


def install_check():
    """Confirm the files and the short-path interpreter."""
    model_files()
    python = runtime_python()
    if python.is_symlink() or not python.is_file() or not os.access(python, os.X_OK):
        raise SystemExit(
            'Paradee runtime is not installed: ' + str(python)
            + '. The short-path interpreter and en_core_web_sm are installed by the '
            'image upgrade (provision/install-image-models.py --install-paradee-runtime), '
            'which needs root. This call does not install them.')
    print('Paradee installed: ' + PARADEE_ID)
    return 0


def synthesize(text, voice, wav_path):
    model, config, _licence = model_files()
    python = runtime_python()
    synth = Path(__file__).resolve().with_name('paradee_synth.py')
    if synth.is_symlink() or not synth.is_file():
        raise SystemExit('Paradee synthesizer is missing')
    subprocess.run([str(python), '-I', str(synth), '--model', str(model), '--config', str(config),
                    '--voice', voice, '--text', text, '--wav', str(wav_path)], check=True)


def play(wav_path, volume):
    if volume <= 0:
        return
    paplay = shutil.which('paplay')
    if paplay:
        level = max(0, min(20, int(volume))) * 65536 // 20
        subprocess.run([paplay, '--volume=' + str(level), str(wav_path)], check=False)
        return
    aplay = shutil.which('aplay')
    if aplay:
        subprocess.run([aplay, '-q', str(wav_path)], check=False)


def _wav_ok(path):
    with wave.open(str(path), 'rb') as handle:
        return handle.getnframes() > 0 and handle.getframerate() == 24000


def session(argv):
    parser = argparse.ArgumentParser(prog='paradee-session')
    parser.add_argument('--system-voice', action='store_true')
    parser.add_argument('--system-volume', default='20')
    parser.add_argument('--speak', default='')
    args, _unknown = parser.parse_known_args(argv)
    try:
        volume = int(args.system_volume)
    except ValueError:
        volume = 20
    saved = saved_selection()
    voice = saved.get('voice') or DEFAULT_VOICE
    if voice != DEFAULT_VOICE and not str(voice).startswith(('af_', 'am_', 'bf_', 'bm_')):
        voice = DEFAULT_VOICE
    # The greeting names af_heart. Another saved Paradee voice still speaks,
    # and the ready line stays the fresh-install sentence only for af_heart.
    ready = 'System voice ready: paradee ' + voice
    spoken = args.speak
    with tempfile.TemporaryDirectory(prefix='paradee-') as temporary:
        wav_path = Path(temporary) / 'speech.wav'
        if spoken:
            synthesize(spoken, voice, wav_path)
            if not _wav_ok(wav_path):
                raise SystemExit('Paradee wrote no audio')
            play(wav_path, volume)
        print(ready, flush=True)
        while True:
            piece = sys.stdin.buffer.read(1)
            if piece != b'g':
                return 0
            synthesize('Goodbye.', voice, wav_path)
            play(wav_path, volume)
            print(GOODBYE, flush=True)
            return 0


def engine():
    """Exit 0 when the saved selection is Paradee, so the launcher can intercept."""
    saved = saved_selection()
    if saved.get('engine') == 'paradee':
        print(saved.get('voice') or DEFAULT_VOICE)
        return 0
    return 1


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    command = argv[0] if argv else ''
    if command == 'engine':
        return engine()
    if command == 'install':
        return install_check()
    if command == 'session':
        return session(argv[1:])
    raise SystemExit('usage: paradee_runtime.py engine|install|session')


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError as error:
        raise SystemExit(error.returncode) from error
