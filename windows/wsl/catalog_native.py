"""Explicit Windows capabilities, separate from Linux application binaries."""
import base64
import os
from pathlib import Path
import subprocess
import sys
from urllib.parse import urlsplit


def main(args):
    if os.name != 'nt':
        raise ValueError('These handlers require native Windows')
    if len(args) != 2:
        raise ValueError('Pass one URL, file path, or quoted speech text')
    name, value = args
    if name == 'windows-browser':
        url = urlsplit(value)
        if url.scheme not in ('https', 'http') or not url.netloc:
            raise ValueError('The browser requires an HTTP(S) address')
        os.startfile(value)
    elif name in ('windows-pdf', 'windows-media'):
        path = Path(value).resolve(strict=True)
        extensions = {'.pdf'} if name == 'windows-pdf' else {
            '.mp3', '.wav', '.ogg', '.flac', '.m4a', '.aac', '.mp4', '.mkv', '.webm', '.avi', '.mov'}
        if not path.is_file() or path.suffix.lower() not in extensions:
            raise ValueError('Pass a supported document/media file')
        os.startfile(str(path))
    elif name == 'windows-voice':
        # User text is data in the environment, never PowerShell source.
        script = ('Add-Type -AssemblyName System.Speech; '
                  '$voice = New-Object System.Speech.Synthesis.SpeechSynthesizer; '
                  'try { $voice.Speak($env:KILIX_CATALOG_SPEECH) } finally { $voice.Dispose() }')
        return subprocess.call(['powershell.exe', '-NoProfile', '-NonInteractive', '-EncodedCommand',
                                base64.b64encode(script.encode('utf-16le')).decode()],
                               env=dict(os.environ, KILIX_CATALOG_SPEECH=value))
    else:
        raise ValueError('Unknown Windows handler')
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main(sys.argv[1:]))
    except (ValueError, OSError) as error:
        print('kilix: ' + str(error), file=sys.stderr)
        raise SystemExit(2)
