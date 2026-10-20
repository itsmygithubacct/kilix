# Kilix 0.2.2 native Windows preview

This builds the actual Kilix graphical application for Windows x64: native
Win32/WGL windowing, the RC6-era Kitty parser and renderer, Kilix pane chrome,
tabs/splits, and ConPTY sessions. PowerShell, Command Prompt, and installed WSL
distributions run inside the same application. WSL is optional.

The published `main` baseline and exact engine gitlink are in `baseline.json`.
The Windows backend adapts [ecstra/win-kitty](https://github.com/ecstra/win-kitty)
revision `c024f6fa564072e1d040db019ee5fa00d8a90c87`; attribution is preserved in
`src/WINDOWS-PORT-NOTICE`. Pending private RC6 work is not included.

## Repository and preview downloads

Windows and Linux share `main` in the existing Kilix repository and
[Kitty fork](https://github.com/itsmygithubacct/kitty).
`work/windows-0.2.2` remains available for Windows development.
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
checks cannot replace those hardware tests. See [preview notes](RELEASE-NOTES.md)
and the bundled [Notepad example](examples/README.txt).

For a source checkout, preserve LF line endings for the MSYS2 and WSL scripts:

```sh
git clone --config core.autocrlf=false --branch main https://github.com/itsmygithubacct/kilix.git
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

## Windows desktop automation

`kilix desktop` (alias `kilix windows`) controls Windows applications. Terminal
pane and tab commands remain separate. The native command and WSL companion
share the `kilix_sdk.windows` client, validation and offline help:

```powershell
kilix desktop --help
kilix desktop capabilities --json
kilix desktop status --json
kilix desktop windows --title "Editor" --json
kilix desktop inspect --window 123456 --depth 3 --limit 200 --json
kilix desktop focus --window 123456
kilix desktop type --window 123456 'Hello, 世界'
kilix desktop key --window 123456 CTRL A
kilix desktop click --window 123456 --x 80 --y 100 --button left --count 1
kilix desktop scroll --window 123456 --x 80 --y 100 --delta -120
kilix desktop move --window 123456 --x -800 --y 40 --width 800 --height 600
kilix desktop screenshot 'C:\Temp\desktop.png'
kilix desktop screenshot --window 123456 'C:\Temp\window.png'
kilix desktop screenshot --local '.\window.png' --window 123456
kilix desktop launch --cwd 'C:\Temp' -- notepad.exe 'C:\Temp\notes.txt'
```

Replace example HWNDs with handles returned by `windows` or `launch`. Window
listings include title, class, physical rectangle, minimized state, process
path when available, and `target: {hwnd, pid, started}`. `started` is the
process creation FILETIME as a decimal string. For a saved identity, supply
all three flags: `--hwnd 123456 --pid 4321 --started 134000000000000000`.
The native worker checks that identity before acting, including PID reuse.
The convenient `--window HWND` resolves an exact handle with a fresh listing;
it cannot detect reuse that happened before that listing. Use the full saved
identity when an action must address a previously observed process.

Use `find --app editor.exe --title Notes` to discover matching windows;
application basename/path matches exactly ignoring case, while title matches
a substring ignoring case. Actions accept the same filters (and `--pid`) and
resolve a unique full identity before dispatch. Multiple matches or incomplete
discovery are refused. UIA `--name`, `--automation-id` and `--control-type`
are exact, case-sensitive, conjunctive filters on `find`, `invoke`, `set-value`
and `wait`. For example:

```powershell
kilix desktop find --app notepad.exe --title notes --json
kilix desktop focus --app notepad.exe --title notes
kilix desktop find --app notepad.exe --title notes --control-type Edit --json
kilix desktop wait --app notepad.exe --title notes --control-type Edit --for enabled --wait-ms 5000 --json
kilix desktop set-value --app notepad.exe --title notes --control-type Edit 'Example text'
```

These control examples require an application version that exposes a unique
UIA Edit control; inspect/find the actual provider first. `wait` polls inside
one read-only native invocation, including over SSH. It supports `present`,
`visible`, `enabled` and `closed`; a condition deadline returns `wait_timeout`.
See [targeting and waits](desktop-targeting.md) for precise state/identity
semantics, SDK examples, completeness flags, limits and JSON response shapes.

`inspect` returns flat UI Automation nodes with provider runtime `id`, `name`,
`automation_id`, `control_type`, `rect`, `enabled`, `offscreen` and supported
`patterns`. Ordinary ValuePattern fields include `value` (up to 4096 UTF-16
units), `value_truncated` and `value_unavailable`. Pass the returned ID literally to `invoke ELEMENT` or
`set-value ELEMENT TEXT`, together with the target flags. IDs are scoped to
the target's current UI tree; stale or ambiguous IDs fail. Password values
are never read or exposed, and `set-value` refuses password fields.

`focus`, `move`, `minimize`, `maximize`, `restore`, `close`, `type`, `key`,
`click`, `scroll`, `inspect`, `invoke` and `set-value` require a target (or CLI/SDK
window filters that uniquely resolve it).
`screenshot` optionally takes one; `status`, `windows` and `launch` do not.
`close` sends WM_CLOSE only: an application can show a save prompt or stay
open. `launch` takes 1–64 literal argv entries and an optional absolute Windows
cwd. It reports the PID and a matching target when available; a started
process or an absent window does not establish application task completion.
Use `--` before launch argv so child options such as `--help` stay literal.
There is no shell expansion or implicit shell invocation.

Input requires the target to already be foreground; use `focus` explicitly
when needed. Mouse points must belong to that target, so an overlapping
window is refused. Coordinates are physical pixels: `move` uses signed
desktop coordinates; `click` and `scroll` use offsets from the target window.
Move dimensions are 1–16384 pixels; the result reports actual bounds and
accepts one-pixel rounding by legacy applications at fractional display scales.
Wheel delta is an integer from -1200 to
1200; positive values scroll upward. `key` accepts a chord such as `CTRL A`
or `CTRL+A`, and named keys including ENTER, SHIFT, ALT, WIN, TAB, ESC,
BACKSPACE, DELETE, HOME, END, PAGEUP, PAGEDOWN, arrows and F1–F12, plus ASCII
letters/digits. `capabilities` lists the complete supported set. Text and
set-value are limited to 16384 UTF-16 code units. Inspection defaults to
depth 3 / 200 nodes, with maxima of 8 / 500.

`screenshot --local PATH` writes a PNG on the caller: the native Windows
process, WSL filesystem, or Linux SSH caller. Paths may be relative or absolute
and must end in `.png`, with an existing parent directory. In WSL, positional
Linux paths automatically use local capture, including `/mnt/c/Temp/screen.png`.
Positional Windows drive or UNC paths retain the native Windows file operation.
For example, `screenshot 'C:\Temp\screen.png'` saves on Windows even over SSH.
Use either a positional path or `--local PATH`.

Local capture validates the destination before requesting pixels, checks the
returned PNG header/dimensions, byte length and SHA-256, then atomically publishes
the complete file. Existing files are refused unless `--overwrite` is explicit,
including files created by a competing writer. Destination symlinks and special
files are refused. Normal human and JSON command results contain metadata only:
`path`, `byte_length`, `sha256`, physical `bounds: {x,y,width,height}`, `capture`
and `occlusion_possible`. Local PNG payloads are bounded to 32 MiB; response
transports allow 48 MiB for base64 and metadata. The existing Windows file
operation retains its physical size limits without the PNG payload limit.
On Linux/WSL, atomic no-clobber publication requires a filesystem with hard-link
support; unsupported filesystems return an I/O error without replacing a file.

Desktop screenshots capture the visible desktop;
obscured/minimized windows and application capture restrictions can affect
what a targeted capture can return.

The backend runs a bounded, own-user interactive scheduled task, writes an
atomic UTF-8 JSON response, and cleans up its owned task. There is no network
server. Its worker starts through Kilix's bundled graphical interpreter without
a console, so starting a command does not take focus away from the target.
The worker compiles its native backend once per source version into
`%LOCALAPPDATA%\Kilix\desktop-cache` (one small DLL, safe to delete) and loads
that build afterwards, saving about 0.75 seconds per request; if the cache cannot
be written or loaded it compiles in memory as before.
`status` reports session/user and interactive/locked availability.
Locked sessions, UAC secure desktops and elevated UI can prevent access;
the command does not manage logins or RDP sessions. Help and `capabilities`
need no live session and never create a task. `--timeout` is 1–60 seconds
(default 20); an uncertain operation is never retried automatically.

Human output is the default. `--json` works before or after the subcommand
(before literal launch argv) and emits the response envelope. Full requests
use `kilix desktop --json --request-json -` with UTF-8 JSON on stdin:

```json
{"schema":"kilix.desktop/v1","operation":"inspect","params":{"depth":3,"limit":200},"target":{"hwnd":"123456","pid":4321,"started":"134000000000000000"},"timeout":20}
```

The exact top-level fields are `schema`, `operation`, `params`, optional
`target`, and optional `timeout`. Unknown fields, incomplete identities and
invalid types/limits fail before transport. Raw requests never resolve bare
handles. Success is `{"schema":"kilix.desktop/v1","ok":true,"operation":OP,
"result":...}`; failure replaces `result` with `"error":{"code":...,"message":...}`
and exits nonzero. CLI usage/validation failures exit 2; execution failures
exit 1. Put the timeout inside a full JSON request. Per-command `--help`
describes its parameters; `capabilities` is a local discovery command, not
a native protocol operation.

The standard-library SDK imports on Windows and Linux without POSIX-only
dependencies. A native caller passes the package root when it is not running
through `native-commands.py`. An external transport supplies a callback taking
one request dict and returning one response dict, including an SSH adapter:

```python
from kilix_sdk.windows import DesktopClient, capabilities, cli_runner

# The SSH endpoint/executable is configured by the caller. Request data goes
# through stdin; it is never interpolated into a remote shell command.
client = DesktopClient(runner=cli_runner([
    "ssh", "windows-workstation", "kilix-cli.exe", "desktop",
]))
inventory = capabilities()                # offline
result = client.call("windows")
# Use a target from result for subsequent calls.
# client.call("inspect", target=target, params={"depth": 2, "limit": 100})
# client.capture_to_local("./screen.png", target=target, overwrite=False, timeout=20)
```

`client.execute(full_request)` returns the envelope; `client.call(...)`
returns its result or raises `DesktopError` with a machine-readable `code`.
Transport errors also raise `DesktopError`. The SDK neither opens an SSH
connection nor selects a host unless the supplied runner does so. The native
default loads exactly the packaged `desktop_transport.py` beside the entry
point. WSL uses `kilix-cli.exe desktop` with fixed argv and JSON stdin.
`client.capture_to_local(path, *, target=None, overwrite=False, timeout=20)`
returns the local metadata described above, or raises `DesktopError`. Omitting
the target captures the desktop. It uses the additive read-only `capture`
protocol operation, whose parameters are empty and target is optional. Its
result adds `png_base64` to the integrity/bounds metadata and has no path.
Raw `--request-json -` and SDK callers can request that payload; there is no
byte-printing `capture` subcommand. No persistent remote PNG is created; the
existing private, transient dispatch response is cleaned up by the transport.

On a Linux Kilix host, `desktop` remains the desktop-provider launcher. Use
`windows --host` for a Windows computer with an existing SSH connection:

```sh
./kilix windows --host windows-workstation status --json
./kilix windows --host windows-workstation windows --json
./kilix windows --host windows-workstation inspect --window 123456 --json
./kilix windows --host windows-workstation screenshot --local ./screen.png
```

SSH uses the host's existing authentication and configuration, including
`ProxyJump` or `ProxyCommand` for an intermediary. The Windows user's managed
Kilix installation is discovered from its install state; for a portable
package, put `--windows-root 'C:\Apps\Kilix'` before the operation. Requests
travel as JSON on stdin. Only one active desktop for that Windows user is
selected; disconnected or ambiguous logins produce an availability error.
The reusable `windows_control.SSHTransport(host, root=None)` callback can also
be passed to `DesktopClient(runner=...)`.

Portable installation paths also travel as JSON data, keeping Unicode paths
out of the remote shell command. SSH reserves startup and cleanup time beyond
the native request timeout. Request writes, process execution and output reads
share that deadline; errors identify the phase when possible. Partial SSH
diagnostics are retained on a timeout. A failed request may already have acted:
inspect the desktop before retrying. Requests are never automatically replayed.

Each SSH reply, including a failure, carries `transport`: a random
`request_id` (also appended to error messages), the host, `total_ms`, and the
phases that ran: `ssh_ms` (connection and remote PowerShell start), `setup_ms`
(the remote helper), `native_ms` (the native desktop command) and `remote_ms`.
The remote side echoes the identifier, and a reply naming another request is
refused.

Source checks without desktop access:

```sh
python3 -B -m unittest discover -s windows/tests -p 'test_desktop_commands.py'
python3 -B -m unittest discover -s windows/tests -p 'test_desktop_capture.py'
python3 -B -m unittest discover -s windows/tests -p 'test_desktop_selectors.py'
python3 -B -m unittest discover -s windows/tests -p 'test_desktop_ssh.py'
```

`tests/test-desktop-backend.ps1` checks native request validation and the bounded
PNG stream without operating a desktop. `tests/desktop-integration.py` runs through the packaged
interpreter and launches its own WinForms fixture for interactive checks; it
requires an unlocked desktop. The Windows 10 x64 qualification at 150% display
scaling covers Unicode text, key chords, clicks/scrolling, UIA inspection and
actions, PNG capture, focus/move/minimize/maximize/restore, stale identities,
and closing the owned fixture. `tests/desktop-waits.py` exercises delayed
enablement/closure, incomplete searches and ambiguous control selection.
`tests/desktop-apps.py` verifies real Notepad Save As contents, opening a file
through Explorer, and an accessible Chrome/Edge form in a disposable profile.
Run these scripts with the packaged Python on an unlocked desktop; the apps
test also takes an evidence-directory argument. Other Windows versions and
mixed-DPI monitor layouts need separate qualification.

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

## Recoverable native sessions

In a native Kilix PowerShell or Command Prompt pane:

```powershell
kilix session new work                  # persistent PowerShell in a new tab
kilix session new build -- cmd.exe      # persistent Command Prompt
kilix session list --json
kilix session detach                    # or detach NAME from another pane
kilix session attach work               # in a new Kilix window after closing one
kilix session restore                   # detached sessions in new tabs
kilix session kill work                 # terminate the session's process tree
```

Persistence is opt-in. New/attach/restore use the invoking instance's
authenticated native control connection; list/detach/kill also work outside
Kilix. Named sessions are shared by the current Windows user. Ordinary panes
retain their original close behavior. A second attachment is refused until the
first disconnects. Exit ends the live shell; attach never starts it again.

An independent user process owns each persistent ConPTY and its Windows job.
The renderer starts it before creating the pane's job, which owns only a byte
relay. Closing a pane/window or losing that relay disconnects the frontend;
the shell keeps its PID, memory, directory and running children. Explicit kill
closes the broker's job and ends its owned process tree. No scheduled task,
system service, administrator access or network listener is installed.

Communication uses local named pipes restricted to the current user's SID and
SYSTEM, with peer identity checks. The terminal frame layouts reuse
kitty-pty-broker; an explicit Windows attach extension preserves full DWORD
exit codes. Up to 64 MiB of output is replayed on reconnect. Overflow discards
older history, and a stalled renderer is disconnected instead of blocking the
broker. Replay omits answered ConPTY cursor/status/device queries while keeping
requests that still need a reply. Exact screen state after truncated history, tab layouts and images
outside the replay are not saved. Windows logoff/reboot, broker termination and
external job policies can still end the session.

Private metadata lives under `%LOCALAPPDATA%\Kilix\native-sessions`, including
the current relay's native control context. `kilix` refreshes that context in
surviving shells after reattachment. Programs that directly cache old instance
credentials may need restarting; retain the installation used by a live broker
until its work has finished. WSL sessions use the separate Linux backend below.

Native process tests run with
`kitty/launcher/kitty-console.exe +launch test.py --module windows_recovery`.
They create only their own sessions and check creator/relay loss, PowerShell
state and output, cmd environment, resize, control refresh, duplicate requests,
malformed frames, deliberate termination and full native exit codes.

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
they never rerun an exited command. Ordinary panes are unchanged. This Linux
backend is separate from native ConPTY persistence above.

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
- All fifteen games rebuilt from the public source bundle and passed a native
  desktop run covering GPU uploads, input, audio streams and exit behavior.
  The media adapter also passed GPU uploads, pause/seek and owned audio-stream
  checks. These checks do not establish speaker audibility.
- Native and WSL managed sessions retained their child PID, in-memory state and
  continuing output across actual window closure and reattachment. Native
  recovery also passed malformed-client, duplicate-name, full exit-code and
  blocked-I/O teardown checks.
- Packaged CLI, Tcl/Tk with a clean PATH, Unicode arguments/cwd/input, AltGr and
  synthetic IME positioning passed. The observed display was one monitor at
  150% scaling; clipboard content was preserved by skipping that optional test.

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
Linux desktop services and model inference require separate qualification.
The WSL catalog provides pinned browser, PDF, media, audio and tmux tools,
plus document/model providers with explicit core-only capabilities. The media
build provides pinned private FFmpeg/libplacebo dependencies on Ubuntu 22.04.
Native Windows supports portable graphs, procedural Soundgen and explicit
system browser/PDF/media/SAPI handlers; Linux app/game
binaries remain WSL-only. See [catalog setup and limitations](wsl/README.txt).
Native and WSL recovery are opt-in as described above.
Unsupported Linux chrome widgets are hidden.
The native Windows backend still contains inherited compatibility shims that
need further review before expanding supported capabilities.

The shipped profile enables authenticated WSL control on Windows loopback.
Each native instance generates a random password, forwards it with its endpoint
and public key through WSLENV, and rejects missing or unknown credentials.
The companion calls the native kitten through interop; no LAN listener is used.
The password stays in process environments and, for managed sessions, the
private attachment file described above. It is redacted from companion `@ ls`
output. Child programs inherit control of their terminal instance.
Set `allow_remote_control no` in user configuration to disable this feature.
The separate graphics pipe
rejects network clients and restricts access to the current Windows user and
SYSTEM. Ordinary terminal sessions own Windows jobs whose descendants are
terminated when their panes close. Persistent native brokers retain their own
jobs until the program exits or the user kills the session. Background Linux
jobs inside WSL follow WSL's own
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
insufficient: its Windows backing volume can be full. With `-WslDistribution`,
`wslCompanion` also names the active companion generation and its Needle source
revision, and runs `kilix needle workflows status --json` through the package's
WSL bridge to report whether Needle's model assets are verified. It never
collects profile or configuration contents, history, clipboard text or
environment credentials. Review paths and distribution names before sharing a
report.

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
