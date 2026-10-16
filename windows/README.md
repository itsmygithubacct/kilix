# Kilix 0.2.2 native Windows development build

This builds the actual Kilix graphical application for Windows x64: native
Win32/WGL windowing, the RC6-era Kitty parser and renderer, Kilix pane chrome,
tabs/splits, and ConPTY sessions. PowerShell, Command Prompt, and installed WSL
distributions run inside the same application. WSL is optional.

The published `main` baseline and exact engine gitlink are in `baseline.json`.
The Windows backend adapts [ecstra/win-kitty](https://github.com/ecstra/win-kitty)
revision `c024f6fa564072e1d040db019ee5fa00d8a90c87`; attribution is preserved in
`src/WINDOWS-PORT-NOTICE`. Pending private RC6 work is not included.

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
Inside these WSL sessions, `kilix` invokes the native application using WSL
interop and Windows PATH import. `kilix help` lists all Windows launcher verbs;
`kilix engine --help` lists the engine's full options. Linux Kilix verbs and app
launchers are not installed by the native package. Unsupported verbs report an
error and exit with status 2. Existing shells need reopening after an upgrade.

## Validation

Validated on Windows 10 Pro build 19045, Intel HD Graphics 620, OpenGL 3.3,
PowerShell 5.1, and Ubuntu under WSL2. The native engine compiled with GCC 16.2,
CPython 3.14.8, and Go 1.27.2.

- Packaged desktop test with a clean system PATH: real PowerShell, cmd, and WSL
  output; three panes, two tabs, GPU image uploads from both Windows and WSL;
  successful process exit after child completion.
- Direct native-launcher tests: PowerShell and `--wsl Ubuntu`, input round-trip,
  new default-shell splits, and closing the owned sessions.
- Selected native engine suites: screen, parser, graphics, layout, options, and
  Windows PTY ownership: 101 tests, 98 passed and 3 explicit skips. This is not a
  claim that every upstream Go/Python test or Linux-specific feature passes.
- Engine PTY regression: repeated successful sessions and failed spawns return
  to the same process handle count; closing a session terminates its owned child.
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

## Scope and remaining work

This is a usable development port, not a fully qualified Windows release.
Linux desktop services, in-pane browser, voice tools, and the persistent PTY
broker/recovery are unavailable. Unsupported Linux chrome widgets are hidden.
The native Windows backend still contains inherited compatibility shims that
need further review before expanding supported capabilities.

Remote control is disabled in the shipped profile. The separate graphics pipe
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
