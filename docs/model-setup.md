# Model setup

## Explicit model setup

`kilix models list` and `kilix models show ID` inspect the pinned Content
catalog without initializing settings, installation directories or receipts.
The catalog file is parsed only after the pinned Content component has checked
its bytes against the digest it ships; a component that cannot make that check
is refused rather than trusted. The plan shows the exact model/version, source,
licence identities, disk allowances, the receipt store the whole stack shares
and the actual host root. An inherited `KILIX_CONTENT_ROOT` does not override
that root, and neither command creates any of those directories.

```sh
kilix models list
kilix models show encodec-24khz-stateful
kilix models verify yolox_s
kilix models install whisper-tiny-ggml
kilix models install whisper-tiny-ggml --from /media/usb/whisper-tiny-ggml
```

`kilix models verify ID` checks the existing licence receipt against the exact
manifest and verifies every declared installed file. It creates no directories,
changes no receipts, and performs no download or inference. Missing agreement
or missing, altered or symlinked files beneath the installer root fail the check.

For object detection, `kilix install yolox` prepares ONNX Runtime and a
320-pixel model cut, then records a detector wrapper for `kilix look` and NVR.
`KILIX_YOLOX_MODEL=yolox_nano kilix install yolox` selects nano; s is the
default and tiny is also supported. Explicit reinstallation refreshes the
selected model and module path. The installed wrapper names the exact cut;
changing the input frame geometry does not silently select a native export.
It clears the adapter's model environment override. `kilix yolox check`
requires the existing agreement and byte binding for the export, cut, tools,
checksum list and wrapper, plus the source checkout's commit. A Git source
must be clean when bound; an explicitly trusted directory without Git records
a null revision and binds only its file bytes. Older runtimes without this
binding need one explicit reinstall. These integrity checks do not certify
inference or model accuracy.

For controlled pipe comparisons, `kilix yolox run --geometry 320x320 --conf
0.25 --nms 0.45 --threads 2` verifies the runtime and reads fixed BGRA frames
from stdin. Its stdout is binary float32[20][6]. `--per-class` changes NMS to
suppress only within the winning class at each cell; it does not create
multiple class predictions per cell. The default suppresses across classes.
The score is objectness times the winning class probability, with a strict
`score > conf` filter and at most 20 replies. A caller's `min_score` is a
second filter and cannot recover rows already removed by `--conf`.

The object consumer area-averages and centres each frame or crop in a black
320 square. The adapter keeps BGR order and raw 0–255 values; its top-left,
fill-114 nearest-neighbour letterbox is an identity for that square and its
320 cut. Sending an original rectangular frame directly to the adapter is a
different preprocessing path. Comparing native resolution requires an
explicit model path to the module tool outside the installed-cut launcher.

`kilix yamnet run` verifies and executes the existing sound provider without setup or selection writes. Selected YOLOX wrappers also verify their bound source/cut and Content agreement on direct invocation. Copying a YOLOX runtime to a new path requires `kilix install yolox` there to regenerate its binding; a sound runtime can relocate on its recorded Python/platform ABI.

Installation requires an interactive terminal and explicit confirmation; there
is no `--yes` option or piped consent. The licence screen is the licence
authority's own and is printed verbatim before anything is recorded, including
its marker for a bound text that changed since an earlier acceptance. An
informational record asks for no licence acceptance at all. An affirmative one
asks separately, after the install confirmation, and is satisfied only by
typing the exact agreement line shown; anything else, including an empty line,
declines and writes nothing. Unknown, altered or unsafe terminal text is
refused before any prompt is printed. No checkpoint or its derivatives are
bundled with this command.

Acceptance is recorded as one receipt bound to the model's manifest and to the
licence record behind its first licence. A model whose catalog record names a
second licence has that licence's text printed in full and labelled as bound
by nothing. Receipts are read and written at the one root
`kilix_license.receipt_store_root()` reports — `$KILIX_LICENSE_RECEIPTS` if set,
otherwise `$GPU_TERMINAL_HOME/license-receipts`, otherwise
`$HOME/.local/gpu_terminal/license-receipts` — so this command and the gates that consume
receipts cannot file and look in different places. Re-running `install` for a
model an existing receipt already covers installs it without asking again, and
a receipt written for a different manifest does not cover the new one.

`kilix models install ID --from DIR` installs from files you already hold
instead of downloading them, for an air-gapped or metered machine. `DIR` holds
the model's files at their manifest paths, which is the layout an installed
copy has, so a tree copied from another machine can be given unchanged. It is
the same install, not a second one: the same licence screen, which adds the
directory it reads and `download: none`, the same typed agreement and the same
receipt, and the pinned component verifies every file against the manifest
before anything is published; licence notices are written from the licence
authority, never taken from `DIR`. Nothing is downloaded on any path under
`--from`, including a model an existing receipt already covers. A missing or
unreadable directory is refused, naming it, before anything is shown or
recorded; a directory whose files do not match is refused after the typed
agreement and installs nothing, and the acceptance typed by then stays
recorded, as it does when a download fails. The receipt records that you
accepted the licence, or for a notice licence that its terms were shown and no
agreement, exactly as for a download. It does not record that you supplied the
files. A pinned component without the supply surface refuses
`--from` by name.

Explicit `--root /absolute/installer/root` before the subcommand targets
another actual installer root, for example Kilix 95's separate apps root; it
does not migrate models or reinterpret receipts.

Setup uses the pinned component's own bounded acquisition, conversion and
atomic selection APIs, and its staged tree must match the record's manifest
before it is published. Model availability still depends on the selected
release's working delivery sources; an unpublished or unavailable source is
not treated as installed. Disk allowances are not measured RAM/VRAM fit, and
installation is not provider readiness or performance qualification. Ordinary
Amp/remote/voice launches never invoke this setup UI, acquire models or accept
terms automatically. The per-command setup timeout defaults to 900 seconds
and can be set from 1 to 3600 with `install --timeout`.

## Model setup wizard

Run `kilix wizard` (or `kilix wizard --tui`) for the terminal wizard. Kilix95
uses the same saved choices in its startup window and **Model Setup** menu.
Each unanswered model category offers checkbox alternatives, download/disk
sizes, and available sizing recommendations. Accept or decline, then Continue.
Speech, dictation, workflows, YOLOX vision, EnCodec audio, Bonsai images, sound
recognition, System-local LLM for Avatar, other language models, embeddings,
documents and visual chat each have a page. RAM/VRAM estimates that the sizer
does not provide are shown as unavailable; a default is not a fit guarantee.

Choices are saved before downloads begin. At the end, licence texts and all
model-specific conditions are presented in groups, with one Yes/No question
naming the affected models. Declining a group skips its models. Kilix95 hosts
this agreement terminal inside the desktop. Accepted models receive individual
receipts; microphone permission remains a separate question for dictation.
Installing a catalog asset does not enable a runtime that does not support it.

System-local LLM choices require a running local Ollama backend. The wizard
checks backend readiness before preselecting a recommendation and explains
missing prerequisites. Choices remain available to save for later setup;
opening the wizard does not install or start an Ollama service.

If a model acquisition fails, the wizard continues with other accepted models
and completes categories whose selected models are ready. It reports each
failure and keeps incomplete categories pending with their choice order intact.
Retrying `kilix wizard finish` skips completed categories and reuses still-valid
licence receipts. Runtime setup failures also leave their category pending
while other categories finish. A failed batch still returns a failure status
and saves its completed categories and individual failures in the result log.

Selected diarization and Pocket TTS models need optional conversion software.
After licence consent, the wizard shows the pinned OS installer's download
details and asks separately before running its administrator step with `sudo`.
It resolves every selected converter before downloading model weights. If
software setup is declined or fails, choices remain pending: retry
`kilix wizard finish`, or revisit the model page to change the selection.
Listing pages and saving choices never invoke these installers.

Useful CLI routes:

```sh
kilix wizard --json                 # inspect remaining pages and pending choices
kilix wizard --all                  # revisit saved choices
kilix wizard answer vision yes --model yolox_nano
kilix wizard answer audio no
kilix wizard finish                # terminal licence batch and installation
```

Repeat `--model` to select alternatives together. The recommended selection is
first when checked in the interactive wizard; Avatar uses the first selected
System-local LLM as its default. Closing setup retains saved choices and leaves
unfinished installation available for retry. No model or licence is accepted
by merely opening or listing the wizard.
