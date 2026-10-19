# Kilix 0.2.2 native Windows development build

This builds the actual Kilix graphical application for Windows x64: native
Win32/WGL windowing, the RC6-era Kitty parser and renderer, Kilix pane chrome,
tabs/splits, and ConPTY sessions. PowerShell, Command Prompt, and installed WSL
distributions run inside the same application. WSL is optional.

The published `main` baseline and exact engine gitlink are in `baseline.json`.
The Windows backend adapts [ecstra/win-kitty](https://github.com/ecstra/win-kitty)
revision `c024f6fa564072e1d040db019ee5fa00d8a90c87`; attribution is preserved in
`src/WINDOWS-PORT-NOTICE`. Pending private RC6 work is not included.

## Repository and preview downloads

Windows is developed alongside Linux in the existing Kilix repository, on
`work/windows-0.2.2`. Native engine changes use the same branch name in the
[Kitty fork](https://github.com/itsmygithubacct/kitty/tree/work/windows-0.2.2).
The `src` gitlink pins the exact engine revision; builds do not track a moving
branch tip. Shared fixes belong in these repositories so both platforms can
receive them together.

The [Windows preview workflow](https://github.com/itsmygithubacct/kilix/actions/workflows/windows.yml)
builds the portable x64 application and checks its command interface. It also
runs the WSL companion regression suite on Linux. Download the artifact from a
successful run for the desired commit; artifacts are retained for 30 days.
Each includes the application ZIP, matching host and engine source archive,
revision record, toolchain package list, and SHA256 checksums. Check the ZIP
against `SHA256SUMS` and extract it completely before launching `Kilix.exe`.

Tags matching `windows-v*-preview.*` (for example `windows-v0.2.2-preview.1`)
prepare a **draft prerelease**; stable `windows-v0.2.2` tags prepare a **draft release** in this same repository after the Windows jobs
pass. Maintainers review the separate Linux CI result and run the interactive
desktop, GPU and WSL tests before publishing a preview. Hosted Windows CLI
checks cannot replace those hardware tests. Windows development stays on its
branch until Linux and Windows validation supports merging to `main`.

For a source checkout, preserve LF line endings for the MSYS2 and WSL scripts:

```sh
git clone --config core.autocrlf=false --branch work/windows-0.2.2 https://github.com/itsmygithubacct/kilix.git
cd kilix
git -c core.autocrlf=false submodule update --init --recursive src third_party/kilix-content third_party/kilix-transcript-clean
git -C src config core.autocrlf false
```

## Build on Windows

From this directory in PowerShell 5.1 or newer:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\provision-engine-toolchain.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File .\package.ps1 -EngineDirectory ..\src
powershell -NoProfile -ExecutionPolicy Bypass -File .\test-application.ps1 -ApplicationDirectory ..\src\dist\kilix -WslDistribution Ubuntu
powershell -NoProfile -ExecutionPolicy Bypass -File .\test-startup.ps1 -ApplicationDirectory ..\src\dist\kilix -WslDistribution Ubuntu
powershell -NoProfile -ExecutionPolicy Bypass -File .\test-cli.ps1 -ApplicationDirectory ..\src\dist\kilix
```

The build uses a separate per-user MSYS2 MinGW64 toolchain under
`%USERPROFILE%\kilix-build\msys64`. It compiles Windows PE executables and native
Windows CPython extensions. The application does not require MSYS2 or a Linux
kernel at runtime. No global PATH changes, administrator rights, or reboot are
needed. The desktop test requires the build user to be logged in interactively.
Omit `-WslDistribution` on a machine without WSL; that excludes WSL validation.

The build pins and verifies the MSYS2 bootstrap, Nerd Fonts 3.4.0 font archive,
and Microsoft ConPTY 1.25.260930003 runtime. Signed MSYS2 packages provide GCC,
Python, Go, and rendering dependencies; `toolchain-packages.txt` records the
installed versions. These rolling package repositories are not a hermetic,
bit-for-bit toolchain lock. The Go module graph remains in the engine's go.mod
and go.sum. `manifest.json` hashes every file in the portable artifact.

Outputs:

- `src\dist\kilix\Kilix.exe`: native graphical launcher.
- `src\dist\kilix\Kilix.com`: native console launcher, including text help/version.
- `src\dist\kilix\wsl-bin\kilix` and `kilix-cli.exe`: WSL command bridge and console executable.
- `src\dist\kilix-0.2.2-windows-x64-dev.zip`: complete portable application.
- `src\dist\kilix\licenses`: engine, dependency, font, and runtime notices.

## Install, update and recover

After checking the downloaded archive against the release's `SHA256SUMS`, extract
it and run the bundled `setup.ps1` from PowerShell 5.1 or later. Source-checkout
users run the same script in `windows/`:

```powershell
.\setup.ps1 -Archive C:\Downloads\kilix-0.2.2-windows-x64-dev.zip -Sha256 VERIFIED_SHA256
# Optional companion, targeting your existing distribution and normal Linux user:
.\setup.ps1 -Action Wsl -WslDistribution Ubuntu -InstallWslDependencies
```

`-WslDistribution Ubuntu` can also accompany the initial install command.
WSL is optional. `-InstallWsl` explicitly requests Microsoft's `wsl --install`
if that distribution is unavailable; Windows may require administrator access,
a reboot and first-run Linux account creation. Setup then reports the exact
retry step. It never creates a root default account or changes existing WSL
configuration. `-WslUser NAME` selects a nondefault Linux user.

The default native destination is `%LOCALAPPDATA%\Programs\Kilix`. Each update
has its own directory under `versions/`; shortcuts point directly at its
`Kilix.exe`. Open windows continue using their retained versions. Setup verifies
the archive digest, safe ZIP paths, every manifested file, and bounded native
CLI health checks before changing active state. Shortcut changes have a recovery
journal. State replacement is the commit point; interrupted changes are repaired
on the next install or explicit recovery. No administrator privileges, permanent
PATH changes, or execution-policy changes are needed for native installation.
If your script policy blocks an unsigned preview, use your normal approved
script-review/unblocking process; setup does not lower that policy.

Use the same destination for upgrades. `-Destination PATH` can point to a prior
installer's directory: its legacy `kilix/` files are retained alongside the new
managed generations. Legacy and extracted portable directories are never moved
or pruned automatically. User configuration remains in
`%LOCALAPPDATA%\Kilix\kitty.conf`; WSL models, receipts, catalog state and saves
stay in their Linux user directories. Generation cleanup is deliberately manual:
close old sessions and retain the active and previous generations listed in
`install-state.json`. Allow disk space for the old installation and the complete
new generation. `-NoShortcuts` suppresses all shortcut writes, including rollback.

```powershell
.\setup.ps1 -Action Status
.\setup.ps1 -Action Rollback
.\setup.ps1 -Action Recover
.\diagnostics.ps1 -VerifyFiles -WslDistribution Ubuntu -OutputPath diagnostics.json
```

Rollback validates and reactivates the previous **managed native** generation;
it does not roll back user data or WSL companion generations. Retrying
`setup.ps1 -Action Wsl -WslDistribution Ubuntu` handles a failed optional companion
step without reinstalling Windows files. The companion retains its own previous
generation if its build fails. Checksums establish integrity against the chosen
release; they do not themselves establish a trusted code-signing identity.

Published updates are available through the installed `update.ps1`:

```powershell
.\update.ps1 -Action Check -Channel stable
.\update.ps1 -Action Apply -Channel preview
# Explicit opt-in only; daily while this Windows user is logged in:
.\update.ps1 -Action EnableSchedule -Channel stable -ScheduleAction Check -DailyAt 09:00
.\update.ps1 -Action DisableSchedule
```

Stable accepts published stable Windows tags; preview accepts stable and preview
Windows tags, ordered by version and preview number. Drafts, unrelated Linux
releases and missing Windows releases produce no available update. Apply fetches
only this repository's release assets, requires one matching ZIP digest in
`SHA256SUMS`, and uses the same transactional installer. It rejects version
regressions and concurrent installation changes during download. It preserves a
previously selected signer thumbprint; `-SignerThumbprint THUMBPRINT` can require
Windows signature validation against an actual trusted publisher certificate.
`-WslDistribution NAME` also updates that companion after the native update;
missing dependencies still require the explicit setup dependency step.

Schedules are never enabled by installation. `-ScheduleAction Apply` explicitly
opts into unattended native updates (and companion updates if selected).
Tasks run only while logged in, using `-ExecutionPolicy Bypass` for that maintenance
process only; no persistent execution policy is changed. Enforced Group Policy
still applies. `update-status.json` records check/apply results. The managed-root
dispatcher reads `install-state.json` on every run and follows the active updater
after upgrades or rollbacks, retaining the chosen schedule, channel and options.
Re-enable to change options or to migrate a schedule created before the dispatcher.
The updater authenticates downloads through GitHub HTTPS and release hashes;
unsigned previews remain unsigned. A changed asset under an already installed
release tag is not treated as a new version.

## Companion provisioning and signing

CI exports `companion.tar.gz` from committed public source pins, includes its
SHA256 sidecar in the application, and also publishes it as a separate artifact.
No model files or personal acceptance receipts are included. For manual builds,
use `python3 windows/wsl/provision.py --output companion.tar.gz`, then pass
`-CompanionArchive PATH -CompanionSha256 HASH` to `package.ps1`. The catalog's
optional `--with-apps` export includes additional pinned application sources.
Setup never fetches a moving branch for application/game sources.

On Ubuntu/Debian, `-InstallWslDependencies` explicitly installs signed distribution
build packages and PulseAudio clients using sudo. Python >=3.12 is used when
available; Ubuntu 22.04 bootstraps checksum-pinned uv 0.12.5 and managed Python
3.12.14 without manually copying uv. Those runtime downloads require network
access; the source bundle and existing managed Python can be used offline through
`wsl/setup.py ARCHIVE SHA256`. Model/license consent remains interactive when
needed and is never manufactured by setup.

`package.ps1 -CertificateThumbprint THUMBPRINT -TimestampServer URL` supports an
actual code-signing certificate with a private key in `Cert:\CurrentUser\My`.
It signs project launchers and PowerShell scripts with SHA256, verifies each
signature and timestamp, then generates the final manifest and ZIP hashes. A
separate signed `manifest-signature.ps1` binds the complete manifest digest, so
signer-pinned installation validates every packaged file, including Python and
vendor runtimes. This proof file is read and verified, never executed.
Vendor signatures and license notices are preserved. Import your certificate
using Windows certificate tooling; passwords/private keys are never accepted on
the package command line or written into artifacts. No certificate is provided
by this repository. Default CI builds are unsigned, and `signing.json` records
that explicitly. A valid signature on the build host does not promise public
SmartScreen reputation or trust on every user's machine. See Microsoft's
[Authenticode signing reference](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.security/set-authenticodesignature?view=powershell-5.1).

The native package includes tkinter, its Tcl/Tk DLL dependencies and data for the
catalog GUI chooser. `test-tk.ps1 -ApplicationDirectory PATH` creates and destroys
a hidden ttk widget with a clean system PATH. WSL GUI availability depends on
the selected Linux interpreter; installing system `python3-tk` alone does not
guarantee Tk in uv-managed Python. The terminal chooser remains available.

The source archive includes the pinned engine and the recursive Content and
transcript-clean source/license closure used by command staging. Export refuses
dirty or mismatched checkouts. Source revisions, companion hashes, toolchain list,
channel metadata and signing status are all covered by the release checksum file.

Extract the entire archive before launching. `README.txt` contains profiles,
shortcuts, and user configuration details. Launch `Kilix.exe --wsl Ubuntu` for a
window whose subsequent splits and tabs also default to that WSL distribution.
The Windows profile provides Ctrl+Shift+U for a new default-distribution WSL tab.
Inside these WSL sessions, `kilix` runs the bundled Python companion using WSL
interop and Windows PATH import. `kilix help` lists WSL commands;
`kilix native help` lists Windows launcher verbs; `kilix engine --help` lists
the engine's full options. Unsupported verbs report an error and exit with
status 2. Existing shells need reopening after an upgrade.

## WSL companion

The companion supports pane/tab listing, focus, splits, new tabs, fullscreen,
watch, authenticated `@` commands, and `--launch PROGRAM [ARGS]`. Linux programs
run in the invoking distribution as the invoking Linux user. Launch arguments
remain separate arguments; `@ launch --env` applies to the Linux child,
including empty values and variable removal. Remote help works without an
active control session (`kilix @ --help`, `kilix @ launch --help`).
Help also works after launch options and for pane/tab commands without a live
connection. `kilix tab --help` lists the tab command family. `kilix fullscreen`
targets the invoking pane's native window through authenticated control.
`--cwd=current` refers to the invoking Linux process's directory. It does not
discover another pane's Linux working directory through Windows process data.
`kilix apps` lists the public application catalog with readiness and platform
limits. `kilix games list` lists installed enabled games. Both support
install/update, enable/disable and a terminal or optional Tk chooser. Arbitrary
installed Linux executables can be launched with `--launch`.

Needle and all fifteen public catalog games install separately into
`~/.local/share/kilix-wsl`. The catalog includes all twelve `kilix-games`
components plus Super Kilix, Plant Grower and Kilix Land, with pinned assets
and dependencies. `kilix games list` lists canonical names; `games play NAME`
runs in this pane and `--launch NAME` opens a new tab. Both accept aliases
such as `chess`, `solitaire`, `lander` and `pong`. Upgrades
build a complete generation before atomically activating it, and retain the
existing model during source-only upgrades. See
[the companion setup instructions](wsl/README.txt). After setup:

```sh
kilix pane list
kilix needle "split right"
kilix needle apps "open pong"
kilix --launch kilix-pong
```

Needle retains its confirmation, target, readiness and model-license checks.
Prompt-sensitive typing requires shell prompt integration; it is refused when
the native pane cannot establish prompt readiness. Linux desktop services and
unintegrated catalog apps remain unavailable.
Catalog source installs cover the browser, PDF viewer, graphs, eSpeak voice
tools and media player; each reports its prerequisites and platform limits.

## Recoverable WSL sessions

After companion setup with a current source bundle, opt a shell or program into
persistence inside a WSL pane:

```sh
kilix session new work               # a new login shell
kilix session new build -- make all  # or a specific program
kilix session list --json
kilix session detach                # inside the managed shell; or detach NAME
kilix session attach work            # in a fresh WSL pane after closing a window
kilix session restore                # detached live sessions in new native tabs
kilix session kill work              # deliberately terminate its processes
```

`sessions` is an alias for `session`. Names contain 1–32 letters, digits,
underscores or hyphens and start with a letter or digit. Sessions belong to one
WSL distribution and Linux user. Creating/attaching needs an interactive
terminal; listing, detaching and terminating also work from a WSL command line.
`restore` needs an authenticated Kilix control connection and skips attached
sessions. A second attachment is refused until the first disconnects.

This reuses the pinned [kitty-pty-broker](https://github.com/itsmygithubacct/kitty-pty-broker).
Its independent process owns the real Linux PTY: closing a Kilix pane/window
disconnects the client and preserves the shell, working directory, in-memory
state and running programs. `attach` and `restore` reconnect existing processes;
they never rerun an exited command. Ordinary panes are unchanged. Native
PowerShell/cmd sessions **are not persistent**: the renderer owns their ConPTY
and kill-on-close Windows job. Retaining native sessions requires a separate
ConPTY owner, which this implementation does not provide.
The [Windows pseudoconsole API](https://learn.microsoft.com/en-us/windows/console/creating-a-pseudoconsole-session)
requires its host to retain and service the communication pipes.

The broker forwards inline graphics unchanged. It retains at most 64 MiB of
replay; overflow discards older screen history and attach reports truncation.
Replayed file/shared-memory images may no longer exist; use inline transfers
and redraw the application if necessary. Native tab layout, renderer-only
scrollback, and exact historical screen/image reconstruction are not saved.
This protects against frontend loss, not Windows reboot/logoff, WSL shutdown or
termination, OOM, or loss of the broker. WSL still controls distribution/VM
lifetime; Kilix does not change WSL configuration or install a keep-alive service.

Private broker data lives in `/tmp/kilix-wsl-UID` (mode 0700). Each live
attachment records its native control context, including password and WSL
interop route, in a mode 0600 file. Companion commands in the surviving shell
refresh that context on every invocation; detached or stale attachments fail
closed. Existing application environments cannot be rewritten: direct Windows
executables or long-running tools that cache old control credentials may need
restarting. Use the `kilix` companion for native control after reconnecting.
Retain the installation used by a live session until its work has finished.

The Linux regression suite builds the pinned broker and destroys a real client
PTY, then verifies unchanged process identity/state, output while detached,
graphics bytes, credential refresh, detach/kill behavior and exact child exit
status. Initialize `third_party/kitty-pty-broker` before running it; set
`KILIX_REQUIRE_RECOVERY_TESTS=1` to treat missing build prerequisites as failure.
Native window-close/reopen and WSL interop checks still need a dedicated Windows
desktop instance; the Linux tests do not establish those hardware results.
`tests/wsl-recovery.py` provides the two-phase desktop probe: set
`KILIX_TEST_ISOLATED=1` and `KILIX_TEST_APP_WSL`, run `start RESULT_DIRECTORY`
inside the first dedicated WSL pane, wait for `ready.json`, close its native
window, and verify `heartbeat.json` advances. In a new dedicated WSL pane run
`attach RESULT_DIRECTORY`; `result.json` records the preserved PID/token and
native pane listing, new-tab and fullscreen checks. On failure, run
`cleanup RESULT_DIRECTORY` to terminate only that probe's session.

## Command interfaces

The command adapters reuse the host's settings, model installer, agent-control,
structured action, transcript replay and tmux modules. `kilix help COMMAND`
and `COMMAND --help` work offline. Native remote help uses the engine's own
parser, including help after launch options; child arguments after `--` remain
literal. The portable package includes the pinned Content/licence and transcript
reader source with their notices.

Inside WSL:

```sh
kilix settings --print
kilix settings --set pane_memory=off
kilix models list
kilix models show needle2
kilix agent-control context
kilix agent-control list
kilix action capabilities
kilix tmux --socket /absolute/path/to/owned.sock --json list
kilix transcript capture --pane 2
kilix transcript list --json
```

`settings` edits the shared **Linux** companion settings. It cannot signal a
Windows PID or reconfigure the native renderer; use `kilix native settings`
for that. Model list/show/verify do not install anything. `models install`
uses the existing explicit licence agreement and receipt checks, with models
under `~/.local/share/kilix-wsl/content` unless `--root` is supplied.
Tmux commands require an explicit socket and never discover a server or
install a manager implicitly. With no arguments, `tmux` shows its available
headless verbs.

Agent-control and action keep their shared JSON schemas and validation. In
their `broker` field the Windows adapter reports an opaque **instance/pane
token**, identified by `identity_kind: windows-instance-pane` in pane listings.
It is not a persistent broker session; reacquire it after a native restart.
Inputs recheck caller/target identity and require a reported shell prompt or
a recognized foreground coding process. The adapter can observe same-user
Linux processes in the invoking distribution when their inherited native
route matches exactly and their controlling tty has one unambiguous foreground
group. Missing process metadata, nested ambiguous ttys and other distributions
remain unknown. Verified delivery additionally retains the shared supported
Codex composer checks. Creation and input acceptance never imply task completion.

From PowerShell or cmd, the bundled native interpreter provides `pane`, `tab`,
`ls`, `focus`, `watch`, `launch`, `fullscreen`, `@`, `status`, `settings`, and
`transcript` without WSL or a system Python installation:

```powershell
kilix help pane
kilix settings --path
kilix settings --set font_size=13.5
kilix settings --reload
kilix launch powershell.exe -NoLogo
kilix transcript clean --pane 2 --format json
```

Native settings validate scalar options with the running package's engine
parser and atomically update `%LOCALAPPDATA%\Kilix\kitty.conf`; `--edit`
opens Notepad for mappings, includes and compound options. Changes apply to
new windows, or to the current authenticated instance with `--reload`.
`setup` and `update` forward literal arguments to the packaged PowerShell
maintenance scripts using process-scoped `ExecutionPolicy Bypass`; persistent
user/machine policy and ordinary shell startup are unchanged. Their help lists
installation, rollback, recovery, update and scheduling actions.

Transcripts support `list`, `path`, `show`, `view`, `clean`, and explicit
`capture`. Capture and `clean --pane ID` see only output the native terminal
still retains. They do not enable continuous ConPTY recording or recover
expired scrollback. Retained `.log` and `.log.zst` files can be read;
compressed files require `zstd`, and interactive `view` requires `less`.
No automatic recording, archive or prune support is claimed. Native models,
agent actions and tmux still require the Linux companion.

## Validation

Validated on Windows 10 Pro build 19045, Intel HD Graphics 620, OpenGL 3.3,
PowerShell 5.1, and Ubuntu under WSL2. The native engine compiled with GCC 16.2,
CPython 3.14.8, and Go 1.27.2.

- Packaged desktop test with a clean system PATH: real PowerShell, cmd, and WSL
  output; three panes, two tabs, GPU image uploads from both Windows and WSL;
  successful process exit after child completion.
- Fullscreen covers the monitor, hides native caption controls, and restores
  the previous normal or maximized placement. The desktop test measures Win32
  window/client rectangles independently of the renderer's fullscreen flag.
- Direct native-launcher tests: PowerShell and `--wsl Ubuntu`, input round-trip,
  new default-shell splits, and closing the owned sessions.
- Selected native engine suites: screen, parser, graphics, layout, options, and
  Windows PTY ownership: 101 tests, 98 passed and 3 explicit skips. This is not a
  claim that every upstream Go/Python test or Linux-specific feature passes.
- Engine PTY regression: repeated successful sessions and failed spawns return
  to the same process handle count; closing a session terminates its owned child.
- WSL companion: authenticated listing, missing/wrong password rejection,
  repeated remote requests, SDK pane launch/close, Needle model-driven split,
  and Pong through both Needle apps and `--launch`. Inline graphics produced
  real GPU uploads and a visible game menu. Linux model-image seals were also
  verified with the companion's portable Python interpreter.
- Independent transport probe: cmd exit status, PowerShell, UTF-8, input, resize,
  Windows argv edge cases, timeout cleanup, graphics escape preservation, and
  actual WSL execution. Hardware probe checks an OpenGL framebuffer readback.

`test-application.ps1` creates a temporary, limited interactive scheduled task,
starts only its own test window, records screen-model/GPU results, and removes
the task. `tests/gui-session.py` exercises GUI actions without enabling remote
control. The engine ownership regressions run through its launcher:

```sh
kitty/launcher/kitty-console.exe +launch test.py --module windows_pty
kitty/launcher/kitty-console.exe +launch test.py --module windows_shell_integration
```

`test-startup.ps1` tests the installed native launcher, shell input, console
version output, and default splits in PowerShell, cmd, and optionally WSL. It
clears the runner's process execution-policy override before launching Kilix,
so the test exercises the user's actual policy. The PowerShell regression also
checks AllSigned, Restricted, and RemoteSigned using temporary process scopes.
Unsigned prompt integration is skipped under AllSigned or Restricted; terminal
input and layouts remain available. Terminal shell profiles retain their execution
policies; scheduled maintenance uses the process-only override described above.

The standalone transport/hardware probes are optional development tools, not
the Kilix application. To build them, run `bootstrap.ps1`, then `build.ps1 -Test
-WslDistribution Ubuntu`. They use pinned Zig 0.17.0. `test-graphics.ps1` runs
the separate WGL probe in the logged-in desktop session.

Companion regression tests run on Linux with Python 3.12 or newer:

```sh
python3 -m unittest discover -s windows/tests -p 'test_wsl*.py'
```

`tests/wsl-integration.py` runs in a dedicated, otherwise unused Kilix WSL
instance. Set `KILIX_TEST_ISOLATED=1`, `KILIX_TEST_APP_WSL` to the Linux path
of the native installation, and `KILIX_TEST_RESULT_WSL` to an existing output
directory. It tests remote help, Linux user/cwd/environment transfer, denied
credentials, quoted and Unicode argv, pane control, fullscreen, Needle and both
Pong launch routes, then closes its created panes. Failure produces a nonzero
exit and an error in feature-results.json.
Renderer GPU upload checks require a native watcher; process survival alone
does not establish that graphics were drawn.

`tests/check-games.py --output checks.json` runs bounded logic, input and
audio checks against the installed catalog. `tests/wsl-games.py` tests each
game in an isolated native WSL instance with `tests/games-session.py` as its
watcher. In addition to the variables above, set `KILIX_TEST_RESULT_DIR` to
the native output directory and `KILIX_TEST_SCRIPT_WSL` to the WSL path of
`wsl-games.py`. Both output paths must refer to the same directory. The test
records actual GPU uploads (or text changes for the two text games), exit via
keyboard or pane closure, and PulseAudio streams, with temporary game profiles. The watcher closes
only its isolated instance. Speaker audibility still requires listening.
The Windows preview workflow also builds and checks the complete pinned
catalog on Linux; native rendering and WSLg audio remain hardware tests.

## Scope and remaining work

This is a usable development port, not a fully qualified Windows release.
Linux desktop services and most public app integrations remain unavailable.
The WSL catalog provides pinned source builds for Chawan, the PDF viewer,
graphs, eSpeak voice tools and the mpv fork. The media build provides pinned
private FFmpeg/libplacebo dependencies on Ubuntu 22.04. Native Windows supports portable
graphs and explicit system browser/PDF/media/SAPI handlers; Linux app/game
binaries remain WSL-only. See [catalog setup and limitations](wsl/README.txt).
WSL recovery is opt-in; native PowerShell/cmd recovery remains unavailable.
Unsupported Linux chrome widgets are hidden.
The native Windows backend still contains inherited compatibility shims that
need further review before expanding supported capabilities.

The shipped profile enables authenticated WSL control on Windows loopback.
Each native instance generates a random password, forwards it with its endpoint
and public key through WSLENV, and rejects missing or unknown credentials.
The companion calls the native kitten through interop; no LAN listener is used.
The password stays in process environments and, for managed WSL sessions, the
private attachment file described above. It is redacted from companion `@ ls`
output. Child programs inherit control of their terminal instance.
Set `allow_remote_control no` in user configuration to disable this feature.
The separate graphics pipe
rejects network clients and restricts access to the current Windows user and
SYSTEM. Each terminal session owns a Windows job; its descendants are terminated
when the session closes. Background Linux jobs inside WSL follow WSL's own
lifetime rules. WSL file-based graphics transfers do not translate Linux paths
into Windows paths; use inline/stream graphics transfers across that boundary.

The bundled Microsoft ConPTY runtime is intentional: the Windows 10 system
runtime leaked a handle per repeated test session on the build machine, whereas
the pinned runtime passed handle-balance checks. `conpty.dll` and
`OpenConsole.exe` must remain beside the engine launcher.

Further release qualification includes additional Windows versions/GPUs,
keyboard layouts and IME, accessibility, clipboard/drag-and-drop coverage,
long-running output/input pressure, and packaging/signing. WSL installation is
an explicit setup option; SSH clients/servers and machine-wide security policy
are not changed.


## Windows integration qualification

`diagnostics.ps1` is read-only apart from an explicitly requested JSON output.
It records OS build, GPU driver, display bounds, shell profile existence and
execution policies, signature status, install state, Windows volume free space,
and optionally WSL tools/filesystem space. WSL virtual disk free space alone is
insufficient: its Windows backing volume can be full. It never collects profile
or configuration contents, history, clipboard text or environment credentials.
Review paths and distribution names before sharing a report.

Run the packaged qualification tools in a dedicated desktop test session:

```powershell
.\test-integration.ps1 -ApplicationDirectory . -ResultDirectory C:\Temp\kilix-qualification
.\test-integration.ps1 -ApplicationDirectory . -AllMonitors -Clipboard
.\test-startup.ps1 -ApplicationDirectory . -WslDistribution Ubuntu
```

The integration runner owns one new window and an input receiver that never
executes pasted text. It checks Unicode input, spaces/Unicode in the child cwd,
and the text-drop handler through ConPTY. It observes native window/display/DPI
and UI Automation capabilities. `-AllMonitors` moves only that test window;
`-Clipboard` tests Unicode clipboard round trips only when the clipboard starts
empty, then restores that empty state unless another application changed it.
Without the flag, or with existing clipboard data, the check is marked skipped.
Startup tests exercise PowerShell/cmd/optional WSL profiles and execution-policy
behavior separately. No global keyboard/mouse input or existing user window is
targeted by the integration runner.

The JSON result deliberately distinguishes observations/skips from passed checks.
Explorer/OLE file drag/drop, keyboard layouts and interactive IME candidate UI,
visual sharpness during mixed-DPI moves, and screen-reader access to terminal text
still need manual acceptance. A UIA window name is not screen-reader support.
The separate input test, when present, exercises the native keyboard path.

The new installer failure/rollback and release-selection fixtures run on Linux
PowerShell and Windows CI; actual package install/upgrade/rollback runs in Windows
CI. These are not hardware qualifications. The recorded hardware baseline remains
Windows 10 build 19045 with Intel HD620 and Ubuntu WSL2. **Windows 11, other GPUs,
mixed-DPI multi-monitor use and accessibility are not claimed tested.** Run and
retain the new qualification results before extending that matrix.
