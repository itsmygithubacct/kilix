"""Offline Paradee synthesis, including reusable sessions and bounded JSONL IPC."""
import argparse
import json
import sys
import time
import wave
from pathlib import Path

SAMPLE_RATE = 24000
MAX_PHONEMES = 510
MAX_TEXT_CHARS = 65536
ESPEAK_PATH_LIMIT = 160


def _data_path():
    import espeakng_loader
    return Path(espeakng_loader.__file__).resolve().parent / 'espeak-ng-data'


def phoneme_chunks(phonemes, limit=MAX_PHONEMES):
    """Keep every non-whitespace phoneme while bounding each model input."""
    if limit < 1:
        raise ValueError('phoneme limit must be positive')
    remaining = phonemes.strip()
    while remaining:
        end = min(limit, len(remaining))
        if end < len(remaining):
            for separators in ('.!?;', ' ,'):
                split = max(remaining.rfind(c, end // 2, end) for c in separators)
                if split >= 0:
                    end = split + 1
                    break
        chunk = remaining[:end].strip()
        if chunk:
            yield chunk
        remaining = remaining[end:].lstrip()


class ParadeeSession:
    """Load the local model/G2P once; calls are serial, voice and speed fixed."""
    def __init__(self, model, config_path, voice):
        import numpy as np
        import onnxruntime as ort
        from misaki import en, espeak

        data_path = _data_path()
        if len(str(data_path).encode()) > ESPEAK_PATH_LIMIT:
            raise ValueError('espeak-ng data path exceeds 160 bytes: ' + str(data_path))
        config = json.loads(Path(config_path).read_text(encoding='utf-8'))
        voices = config['voices']
        if voice not in voices:
            raise ValueError('Paradee has no voice ' + voice)
        self.np, self.vocab = np, config['vocab']
        self.voice, self.voice_id = voice, voices.index(voice)
        british = voice.startswith('b')
        self.grapheme = en.G2P(trf=False, british=british,
                              fallback=espeak.EspeakFallback(british=british), unk='')
        options = ort.SessionOptions()
        options.intra_op_num_threads = 1
        options.inter_op_num_threads = 1
        self.session = ort.InferenceSession(str(model), options, providers=['CPUExecutionProvider'])

    def synthesize(self, text, wav_path):
        started = time.monotonic()
        if not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT_CHARS:
            raise ValueError('text must contain 1 to 65536 characters')
        phonemes, _tokens = self.grapheme(text)
        chunks = list(phoneme_chunks(''.join(c for c in phonemes if c in self.vocab)))
        if not chunks:
            raise ValueError('Paradee produced no supported phonemes')
        np = self.np
        samples = 0
        destination = Path(wav_path)
        try:
            with wave.open(str(destination), 'wb') as handle:
                handle.setnchannels(1)
                handle.setsampwidth(2)
                handle.setframerate(SAMPLE_RATE)
                for index, chunk in enumerate(chunks):
                    identifiers = [0] + [self.vocab[c] for c in chunk] + [0]
                    waveform = self.session.run(None, {
                        'input_ids': np.array([identifiers], np.int64),
                        'speed': np.array([1.0], np.float32),
                        'voice': np.array([self.voice_id], np.int64),
                    })[0].reshape(-1)
                    if waveform.size == 0 or not np.isfinite(waveform).all():
                        raise ValueError('Paradee produced no valid audio')
                    if index:
                        pause = int(0.08 * SAMPLE_RATE)
                        handle.writeframes(b'\0\0' * pause)
                        samples += pause
                    handle.writeframes((np.clip(waveform, -1.0, 1.0) * 32767).astype('<i2').tobytes())
                    samples += int(waveform.size)
        except BaseException:
            destination.unlink(missing_ok=True)
            raise
        return {'samples': samples, 'voice': self.voice, 'sample_rate': SAMPLE_RATE,
                'audio_seconds': samples / SAMPLE_RATE, 'phoneme_chunks': len(chunks),
                'synthesis_seconds': time.monotonic() - started}


def synthesize(model, config_path, voice, text, wav_path):
    """Backward-compatible one-utterance interface used by the supervisor."""
    return ParadeeSession(model, config_path, voice).synthesize(text, wav_path)


def serve(session, source, output):
    """One request and one response per line; no network or implicit downloads."""
    while True:
        line = source.readline(1024 * 1024 + 1)
        if not line:
            return 0
        if len(line) > 1024 * 1024:
            raise ValueError('JSONL request exceeds 1 MiB')
        try:
            request = json.loads(line)
            if not isinstance(request, dict) or not isinstance(request.get('wav'), str) or not request['wav']:
                raise ValueError('request requires text and a wav destination')
            result = dict(session.synthesize(request.get('text'), request['wav']), ok=True)
        except Exception as error:
            result = {'ok': False, 'error': str(error)}
        output.write(json.dumps(result) + '\n')
        output.flush()


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', required=True)
    parser.add_argument('--config', required=True)
    parser.add_argument('--voice', required=True)
    parser.add_argument('--text')
    parser.add_argument('--wav')
    parser.add_argument('--jsonl', action='store_true', help='Reuse the model for stdin {text,wav} lines')
    args = parser.parse_args(argv)
    if args.jsonl and (args.text is not None or args.wav is not None):
        parser.error('--jsonl cannot be combined with --text or --wav')
    if not args.jsonl and (args.text is None or args.wav is None):
        parser.error('--text and --wav are required without --jsonl')
    session = ParadeeSession(args.model, args.config, args.voice)
    if args.jsonl:
        return serve(session, sys.stdin, sys.stdout)
    print(json.dumps(session.synthesize(args.text, args.wav)))
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
