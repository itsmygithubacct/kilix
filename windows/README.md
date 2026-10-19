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
prepare a **draft prerelease** in this same repository after the Windows jobs
pass. Maintainers review the separate Linux CI result and run the interactive
desktop, GPU and WSL tests before publishing a preview. Hosted Windows CLI
checks cannot replace those hardware tests. Windows development stays on its
branch until Linux and Windows validation supports merging to `main`.

For a source checkout, preserve LF line endings for the MSYS2 and WSL scripts:

```sh
git clone --config core.autocrlf=false --branch work/windows-0.2.2 https://github.com/itsmygithubacct/kilix.git
cd kilix
git -c core.autocrlf=false submodule update --init src
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

`install.ps1 -Archive <zip> -Sha256 <verified archive digest>` extracts a
separate per-user copy and creates **Kilix Windows** and **Kilix WSL** shortcuts
on the desktop and Start menu. Existing application directories are retained.
Use `-NoShortcuts` to install only the files.

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
`kilix apps` lists games installed by companion setup. Arbitrary installed
Linux executables can be launched with `--launch`.

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
the native pane cannot establish prompt readiness. Linux desktop services,
catalogue-wide app installation, and Linux session recovery are not ported.

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
input and layouts remain available. Kilix does not lower execution policies.

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
Linux desktop services, in-pane browser, voice tools, and the persistent PTY
broker/recovery are unavailable. Unsupported Linux chrome widgets are hidden.
The native Windows backend still contains inherited compatibility shims that
need further review before expanding supported capabilities.

The shipped profile enables authenticated WSL control on Windows loopback.
Each native instance generates a random password, forwards it with its endpoint
and public key through WSLENV, and rejects missing or unknown credentials.
The companion calls the native kitten through interop; no LAN listener is used.
The password stays in process environments and is redacted from companion
`@ ls` output. Child programs inherit control of their terminal instance.
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
long-running output/input pressure, and packaging/signing. The application does
not install WSL distributions, SSH clients/servers, or modify machine-wide policy.
