# Read aloud and dictation

The speaking-head and microphone controls in Kilix's page strip are the actual
voice actions. The matching desktop-menu entries open settings and diagnostics:
they do not themselves read or dictate. Both actions operate on terminal text.
A pixel application such as the Kilix 95 desktop has neither readable terminal
cells nor a visible text input target, so Kilix refuses both actions there with
an explanation; open or switch to a terminal pane first.
Dictation records the pane selected at the first click and rechecks it before
inserting anything: if it becomes a pixel application or reaches a hidden
password prompt while listening, the transcript is visibly discarded. A
second microphone click requests stop but keeps the private result socket open
for the recognizer's final answer; stale partial text is never substituted.

Opening `kilix stt` installs only the small settings/runtime closure when it is
missing; it does not fetch a recognizer library or model. A click on the
disabled microphone offers the selected model with its download size and asks
before opening a visible installer terminal. The Voice pages in `kilix
settings`, Kilix 95 Settings, and the built-in WM Settings offer the same
install-and-default action. The equivalent scriptable interface is `kilix stt
--models`, `kilix stt --install MODEL`, and `kilix stt --default MODEL`; install
and default may be combined in one invocation.

`kilix stt --models --json` is the shared cross-process catalog boundary for
0.1.9. Its `kilix.speech.models/v1` document reports every choice, exact
download bytes, installed/default state, runtime support, and the common
install-and-default argv without downloading anything. Desktop and chrome
consumers may keep their frozen 0.1.9 fallback tables, but new integrations
must consume this versioned command contract instead of copying the catalog.

The catalog contains `small-en-us` (Vosk, 39.3 MiB), `lgraph-en-us` (Vosk,
124.5 MiB), `vibevoice-asr-bitnet` (VibeVoice, about 1.6 GiB),
`whisper-small-en` (Whisper, 463.6 MiB), `whisper-medium-en` (Whisper,
1459.6 MiB), and `whistle` (Whistle, 16.2 MiB).
Runtime support does not mean the model weights are installed. VibeVoice's
weights are shared with Kilix Bonsai, so installing them does not create a
second copy. VibeVoice dictates once its VibeASR runtime is built (below); it
transcribes each turn when the turn ends rather than showing live partials.

VibeVoice dictation runs through VibeASR.cpp. `kilix voice vibeasr` fetches
upstream commit `c4334009c88060f86cdbbd684b62662f710b6c20` of
microsoft/VibeASR.cpp (MIT) with the llama.cpp revision it records (a
gitlink to XsquirrelC/llama.cpp, the fork carrying VibeASR's BitNet kernels),
builds only `asr_infer` with llama.cpp and ggml linked in (libc, libstdc++ and
libgomp stay the system's), and publishes it at
`$KILIX_DATA_HOME/voice/vibeasr/current/bin/asr_infer`. It needs a C/C++
toolchain and cmake, takes about a minute, and does nothing when that pinned
build is already current. `kilix stt --install vibevoice-asr-bitnet` runs it
after the licence-gated weights are in place.

Whisper dictation runs through `kilix-whisper-stt`, a small provider around
faster-whisper (CTranslate2, int8 on the CPU). `kilix voice whisper` fetches
provider commit `15ef23b32da497a41198d3028e34715801de6196`, installs its
locked environment with `uv sync --locked` under Python 3.12.8, and publishes
it at `$KILIX_DATA_HOME/voice/whisper/current/bin/kilix-whisper-stt`. It does
nothing when that pinned generation is already current. The provider holds no
model and never downloads one; `kilix stt --install whisper-small-en` installs
the licence-gated weights and then runs it. `kilix stt --install
whisper-medium-en` installs the larger English-only model the same way. Medium
has its own licence record, `faster-whisper-medium-en`, with the small model's
MIT terms (OpenAI weights, SYSTRAN CTranslate2 conversion).

`kilix voice install` installs the immutable `kilix-voice` source revision
pinned by `KILIX_VOICE_REF` in `scripts/install-kilix-voice.sh`, the official
Vosk 0.3.45 wheel for the host (x86_64 or aarch64, each pinned by its own digest), and
either the default `vosk-model-small-en-us-0.15` or the
`--model lgraph-en-us` dynamic-graph model. Downloads are SHA-256 verified.
The installer extracts only the wheel's fixed `vosk/libvosk.so` member, checks
its ELF architecture and complete required Vosk API, loads the pinned acoustic
model through that API, and runs every installed tool's `--version` probe before
promotion. Library and model generation names include their full archive
digest. Their active links, the runtime link, command entrypoints, and install
stamp are promoted as one rollback-safe transaction, so a pin change can never
relabel an old payload. `--without-dictation` retains a smaller read-aloud-only
path for non-x86_64 machines.

Speech weights are downloaded only after their licence was shown and
accepted. `kilix models install small-en-us` (or `lgraph-en-us`, or
`vibevoice-asr-bitnet`) does that and records the receipt. Without one,
`kilix voice install`, `kilix stt --install` and `kilix bonsai pull` stop
with exit 3 before fetching the model and name that command.

The native library and acoustic model each carry an adjacent
`README.kilix-provenance` recording their upstream URL and checksum, plus a
copy of the Apache-2.0 license. They remain under the private Kilix data tree;
`kilix voice doctor` prints their active paths. The daemon starts lazily and
uses one private Unix-socket connection per control request. Read-aloud status
is polled four times per second so completion clears the widget, while a new
asynchronous synthesis or playback error is shown once rather than disappearing
into daemon output. Dictation device/model errors, an empty result, or a missing
final response are likewise shown in a dialog instead of looking like a dead
click.

## Neural read-aloud tiers and the speech provider

The `kilix tts` commands these paragraphs describe are in the [command list](install.md#quick-start).

The Qwen CPU and GPU tiers are explicit multi-GiB downloads on x86_64 Debian,
not base-image dependencies. Each is installed only after selection and a
hardware fit check; model weights still require the first-use licence notice.
The GPU tier requires an unmasked Ampere-or-newer NVIDIA GPU 0 and uses locked
CUDA PyTorch and FlashAttention 2. For an existing verified GPU environment,
`KILIX_QWEN_GPU_PYTHON` may instead name its absolute Python path.
The Base tier uses that GPU runtime and its own measured fit profile; no
person's voice is cloned, and its weights require a separate first-use notice.
The three direct `--download-qwen` commands install the locked CPU runtime
and selected weights on first use, then open a foreground speech session.
Repeating the command verifies and reuses the installation. Add `--device
cuda:0` to select the optional GPU runtime explicitly. VoiceDesign uses the
description to create a voice; `/description TEXT` changes it in the session.
VoiceDesign (1.7B) has a length limit on small GPUs. On a physical 6 GiB GPU,
generation plus decode was verified (with random weights) only to 30 s, and the
decoder alone fit at 40 s; generation at 40 s was not established.
Decoder-alone tests ran out of GPU memory at 48 s and 60 s, and generation to
the 60 s cap ran out of memory. Separately, with real weights and streaming on
an emulated 6 GiB limit (the allocator capped at 6 GiB on an 8 GiB RTX 3070),
generation and the final decode finished without running out of memory through
60 s, peaking at 5,712 MiB reserved. There, generation plus final decode
reached the length bound and the worker then refused the job at that bound, so
no job result was produced. The emulation does not stand in for the physical
card. A request
that runs out of memory before any audio is delivered reruns on the CPU,
slowly. A streaming request that runs out after audio has started fails, and
is never retried on the CPU. Keep VoiceDesign requests to 30 s or less on a
6 GiB GPU. This is not a qualified GPU profile.
Piper uses `--interactive --tier neural`; Pocket English/Alba uses
`--interactive --download-pocket`. Each model presents its first-use licence
notice before acquiring weights. `/save FILE.wav` saves the generated audio.
The native Pocket Q8 research variant is not part of these speech paths.
The explicit `--model qwen3-tts-0.6b-customvoice` read-aloud path lazily
installs only the pinned Qwen socket client. Synthesis additionally requires a
separately running, receipt-backed Qwen provider; the audition runtime and its
model files do not satisfy that requirement, and neither is started implicitly.

After installing one or more Qwen models (CustomVoice, 0.6B Base, 1.7B
VoiceDesign) with their licences accepted, for example through
`kilix models install <model>`, explicitly prepare and run the receipt-backed
provider:

```sh
kilix tts provider prepare --uv /absolute/path/to/uv
kilix tts provider serve
# In another terminal:
kilix tts provider status
```

Preparation requires uv 0.12.5 and builds the pinned dependency graph with
managed Python 3.12.8. It checks each Qwen model's current licence coverage and
installed files before building, stages a runtime for every model that passes
(CustomVoice stays the default) and serves them all from one service; a model
without a receipt is left out, and with none the command refuses. Model weights
and acceptance receipts are never created by this command. `--device cuda`
builds the CUDA 12.4 graph instead of the CPU one; such a provider uses NVIDIA
GPU 0 when it is usable and otherwise runs each job on the CPU. The default
`--device auto` picks `cuda` when GPU 0's device nodes exist and it reports
compute capability 7.0 or newer, otherwise `cpu`. Add `--offline` to require already cached sources, Python and
dependencies. Failed preparation leaves the previous selection intact. `serve`
runs in the foreground; Ctrl-C stops the provider and cleans its owned workers.
Status reports the running provider's state and model selection; it does not
certify a new receipt check or release qualification. This route uses the host's
actual model installation root and preserves the receipt and runtime directories
selected during preparation.

For a desktop user service, prepare first, then run:

```sh
kilix tts provider service install  # install only; no startup
kilix tts provider service enable   # start, wait for readiness, then enable at login
kilix tts provider service status
kilix tts provider service disable  # stop and disable login startup
```

`start`, `stop`, and `restart` are also available. The unit selects an immutable
prepared generation; preparing a newer generation does not change a running
service. Stop it before reinstalling, or use `service enable` to stop the old
generation and start the new one. Status probes the generation recorded in the
unit. Startup checks current model receipts before opening the socket, and
readiness must come from the unit's own process. Unit installation preserves
user edits and restores the previous selection if publication fails. Nothing
in this route downloads weights, accepts terms, or enables system-wide startup.

## System voice

Kilix 95's optional startup voice uses the pinned Piper runtime through
`kilix tts --prepare-system-voice` and `kilix tts --system-voice`. First-use setup
shows the model notice in a terminal; the desktop owns the persistent session
and its configurable greeting. No speech model is fetched during a normal
restart of an enabled session.

Enabled system voice polls the shared telemetry ring and local power/link state
about every two seconds. It prepares the five fixed warning clips once, then
plays cached audio without neural synthesis during an incident:

| Condition | Spoken warning | Recovery |
| --- | --- | --- |
| Any temperature sensor above 101°C (213.8°F) | WARNING: the system is overheating | All readings at or below 98°C |
| Discharging battery below 10% | battery low | Charging or at least 12% |
| Discharging battery below 5% | battery critically low | Charging or at least 12% |
| Previously connected physical links all down for 4 seconds | offline | A physical link reconnects |
| Available RAM at most 2%, swap traffic at least 8 MiB/s, and memory PSI `some_avg10` at least 20%, sustained for 10 seconds | heavy swapping | A fresh sample no longer meets the conditions |

Each warning is spoken once per incident, with a five-minute cooldown (one minute
for offline). Starting below 5% announces only the critical battery warning.
Unknown or stale readings do not trigger warnings. Network detection checks
physical carrier, not Internet access; loopback and VPN links do not mask a lost
uplink. Swap occupancy alone does not trigger speech. Swap detection needs Linux
memory-pressure telemetry and consecutive samples no more than five seconds apart.
Temperature thresholds use raw Celsius values regardless of display preferences.
Turning system voice off stops these warnings too. Audio still depends on a
working output device and the process being able to run under resource pressure.

System voice also caches **Goodbye** for normal machine shutdown. On Plebian-OS,
the worker listens to logind and releases its shutdown delay after playback.
The desktop's Shut Down action waits briefly for a voice acknowledgement, with
duplicate speech suppressed when logind also notifies the worker. Desktop-only
restarts, owner-pipe closure and disabling voice remain silent. Generic hosts
need system Python's `dbus` and `gi` bindings for logind notification; the desktop
menu path works without them. Forced shutdown and power loss cannot guarantee
audio playback.

Additional enabled system-voice messages:

- **power connected** / **running on battery**: a known system battery changes
  power source for four seconds. Startup is silent; a fifteen-second per-direction
  cooldown suppresses cable chatter. Peripheral batteries are ignored.
- **disk space low**: the writable local filesystem for `/`, home or Kilix data
  has less than 5% available and less than 2 GiB available. Filesystems are sampled
  every thirty seconds and deduplicated; remote and read-only mounts are skipped.
  Recovery requires at least 8% available or 3 GiB available. The existing
  five-minute incident cooldown prevents repeated warnings.
- **update complete. restart required**: the Plebian-OS whole-stack updater,
  running as the desktop user, commits successfully, completes apt reconciliation,
  and leaves restart to the user. It atomically replaces the private state event
  `system-voice-update-complete`; the running voice worker announces each new
  event once. Automatic restarts, failed updates and notices already present when
  the worker starts are silent. Root-only maintenance updates do not emit a
  per-user voice notice. A notification failure cannot fail an otherwise
  successful update.

All phrases use the existing pre-rendered warning cache; no synthesis is needed
when power changes, disk space is low, or the updater commits.
