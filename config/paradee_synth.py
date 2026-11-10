"""Synthesize one utterance from the local Paradee ONNX file.

The system interpreter starts this with the short-path Paradee environment.
It does not import huggingface_hub and it does not open a network connection.
"""
import argparse
import json
import wave
from pathlib import Path

import numpy as np
import onnxruntime as ort
from misaki import en, espeak

SAMPLE_RATE = 24000
MAX_PHONEMES = 510
ESPEAK_PATH_LIMIT = 160


def _data_path():
    import espeakng_loader
    return Path(espeakng_loader.__file__).resolve().parent / 'espeak-ng-data'


def synthesize(model, config_path, voice, text, wav_path):
    data_path = _data_path()
    if len(str(data_path).encode()) > ESPEAK_PATH_LIMIT:
        raise SystemExit('espeak-ng data path exceeds 160 bytes: ' + str(data_path))
    config = json.loads(Path(config_path).read_text(encoding='utf-8'))
    voices = config['voices']
    if voice not in voices:
        raise SystemExit('Paradee has no voice ' + voice)
    british = voice.startswith('b')
    grapheme = en.G2P(trf=False, british=british,
                      fallback=espeak.EspeakFallback(british=british), unk='')
    phonemes, _tokens = grapheme(text)
    vocab = config['vocab']
    identifiers = [0] + [vocab[character] for character in phonemes if character in vocab][:MAX_PHONEMES] + [0]
    options = ort.SessionOptions()
    options.intra_op_num_threads = 1
    options.inter_op_num_threads = 1
    session = ort.InferenceSession(model, options, providers=['CPUExecutionProvider'])
    waveform = session.run(None, {
        'input_ids': np.array([identifiers], np.int64),
        'speed': np.array([1.0], np.float32),
        'voice': np.array([voices.index(voice)], np.int64),
    })[0].reshape(-1)
    if waveform.size == 0 or not np.isfinite(waveform).all():
        raise SystemExit('Paradee produced no audio')
    pcm = np.clip(waveform, -1.0, 1.0)
    frames = (pcm * 32767.0).astype('<i2').tobytes()
    destination = Path(wav_path)
    with wave.open(str(destination), 'wb') as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(SAMPLE_RATE)
        handle.writeframes(frames)
    return {'samples': int(waveform.shape[0]), 'voice': voice}


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', required=True)
    parser.add_argument('--config', required=True)
    parser.add_argument('--voice', required=True)
    parser.add_argument('--text', required=True)
    parser.add_argument('--wav', required=True)
    args = parser.parse_args(argv)
    print(json.dumps(synthesize(args.model, args.config, args.voice, args.text, args.wav)))
    return 0


if __name__ == '__main__':
    raise SystemExit(main(__import__('sys').argv[1:]))
