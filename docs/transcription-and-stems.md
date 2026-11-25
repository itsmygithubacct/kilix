# Transcription, stem separation and Playalong models

The installed Tiny transcription model has an explicit managed route as well:

```sh
kilix models install whisper-tiny-ggml
kilix transcribe prepare --engine /absolute/path/to/whisper-cli --engine-sha256 SHA256 \
  --decoder /absolute/path/to/ffmpeg --decoder-sha256 SHA256
kilix transcribe serve
# In another terminal:
kilix transcribe status
kilix transcribe file speech.wav --format json
kilix transcribe file speech.wav --stream --format json
```

Preparation requires reviewed whisper.cpp and decoder bytes, uv 0.12.5 and
managed Python 3.12.8. Add `--offline` to require cached sources and Python.
It checks existing model receipts and the full installed model before staging,
copies the tools and manifest into a private generation, and binds its provider,
Content, Licence and Python files. The model stays in Content's installation.
Failed preparation preserves the previous selection. Foreground startup checks
the current receipt and installed bytes before publishing its local socket;
each job checks them again. `record --seconds N` explicitly captures a bounded
microphone recording; file transcription requires no microphone. These tools
provide a CPU development route; hardware, sustained-load and release
qualification remain separate checks.

`--stream` writes unstable transcript revisions to stderr and commits a
corrected final transcript on stdout. For anonymous speaker turns, first run
`sudo plebian-os-install-diarization-converter` on Plebian OS to install the
optional software tool, then `kilix models install kilix-diarization-cpu` and add `--diarization` to
`transcribe prepare`. Each request must explicitly use
`--task diarize --diarization-session --format json`. Its separate speaker
turns preserve recognized words and timestamps; labels belong only to that
request and are discarded with it. Live speaker clustering is unavailable.
The first-use screen shows the exact checkpoint, segmentation licence and
component notices. Installation converts the pinned English CAM++ checkpoint
locally in a bounded CPU process; its optional software tool requires about
187 MiB compressed and 907 MiB installed, with a 2 GiB conversion memory
ceiling. It uses the system's `libgomp1`, `libgfortran5` and `libquadmath0`
packages. Conversion and installation took about 17 seconds on the retained
CPU test host; first use also downloads 34 MiB of approved source models. An
offline converted population can instead be installed with `models install
kilix-diarization-cpu --supplied /absolute/path/to/population` under the same
agreement and digest checks. Runtime inference uses ONNX and does not load
the source pickle or import Torch. Quality and sustained-load qualification
remain separate from these commands.

`kilix split` separates a mix into six stems (vocals, drums, bass, guitar,
piano, other) with the Demucs htdemucs_6s model. `kilix transcribe run --clean`
uses its vocal stem.

```sh
kilix split --install
kilix split song.mp3 --output vocals.wav            # one stem; --stem picks it
kilix split song.mp3 --output stems/ --device cpu   # every stem
```

`--install` installs the pinned Demucs code into a private virtual
environment, then runs `kilix models install demucs-htdemucs-6s`. That screen
shows Meta's weights file, its source and size, and the Demucs MIT text, and
it asks for your answer. The weights download only after you agree. Before
each separation, `kilix split` runs `kilix models verify demucs-htdemucs-6s`.
Demucs then loads the verified file from Content's installation and runs
offline, with its proxies pointed at a closed local port, so it does not fetch
a model itself.

## Playalong's default models

On Plebian-OS, install and upgrade put Kilix Playalong's three default models
in place when they are missing: Demucs htdemucs_6s, Basic Pitch and Whisper
medium.en. All three use the standard distribution policy. Demucs and Basic
Pitch ship in the installation image. Whisper medium.en is downloaded by the
first online upgrade. A machine declines all three in either of two ways:

- answer no on the model wizard's Playalong page, or refuse the licence of
  any of the three on a model wizard page (refusing only Whisper large-v3's
  licence declines nothing);
- set `KILIX_PLAYALONG_MODELS=0` in `~/.local/gpu_terminal/settings.conf`
  (or `GPU_TERMINAL_SETTINGS_FILE`).

`python3 config/playalong_models.py declined` prints the answer that applies.
It exits 0 when the machine declined, 3 when it did not, and 4 when the answer
cannot be read. Plebian-OS installs none of the three when the answer cannot be read.
Whisper large-v3, for lyrics that are not in English, is never a default. The
Playalong wizard page offers it, and it is installed only after you choose it
there.
