Kilix 0.2.2 for Windows — development build

Extract the complete folder and open Kilix.exe. Windows x64 is required.
The renderer is a native Win32/OpenGL application. WSL is optional.

Launch profiles:
  Kilix.exe                 PowerShell
  Kilix.exe --cmd            Command Prompt
  Kilix.exe --wsl            Default installed WSL distribution
  Kilix.exe --wsl Ubuntu     Named installed WSL distribution

Shortcuts:
  Ctrl+Alt+R / Ctrl+Alt+D    Split right / below
  Ctrl+Shift+T              New tab
  Ctrl+Shift+U              New WSL tab
  Alt+arrow                 Focus adjacent pane
  Ctrl+Alt+Z                Maximize/restore pane
  Ctrl+Alt+W                Close pane
  Ctrl+Shift+C / V          Copy / paste
  F11                       Fullscreen

Use the SSH client installed in Windows, or SSH inside WSL, from any pane.
The application does not install or enable an SSH server. WSL setup is optional.
--wsl makes WSL the default shell for subsequent splits and tabs in that window.

User configuration: %LOCALAPPDATA%\Kilix\kitty.conf
This is loaded after config\kilix.conf. No global PATH changes are required.
Inside a Windows Kilix session, `kilix --version` and `kilix --help` print
to the terminal. Kilix.com is the console launcher; Kilix.exe opens the GUI.
The installation directory is added only to Kilix's child-session PATH.
WSL sessions use the bundled extensionless `kilix` companion. This requires
Python 3, WSL interop and Windows PATH import. `kilix help` lists WSL commands;
`kilix native help` lists native commands; `kilix engine --help` lists engine
options. Pane/tab commands control the current native application. Linux
programs launch through the current WSL distribution, preserving their argv.
Needle and games require separate per-user companion dependency setup.
After setup, try `kilix needle "split right"`, `kilix --launch kilix-pong`,
`kilix pane list`, or `kilix apps`. Reopen WSL windows after an upgrade.
Unsupported commands return an error in the terminal.
The bin\kitten.exe helper can be invoked by its full path.

Native CLI (no WSL or separately installed Python required):
  kilix help pane             Offline pane command reference
  kilix pane list             List native panes
  kilix launch PROGRAM ARGS    Literal native program argv in a new tab
  kilix settings --path       Native user configuration location
  kilix settings --set font_size=13.5
  kilix settings --reload     Reload in the current authenticated instance
  kilix transcript capture --pane ID
  kilix transcript clean --pane ID --format json
  kilix status --json
  kilix setup --help          Install/rollback/recovery script options
  kilix update --help         Release checks/apply and opt-in scheduling

Transcripts are explicit snapshots of retained terminal output; automatic
recording and pruning are not enabled. Saved logs can be listed/read/replayed.
Native settings edit kitty.conf; WSL settings use Linux companion storage.
WSL also exposes models, agent-control, action and explicit-socket tmux verbs.
Use kilix help COMMAND for the full offline command reference. Agent input
requires an exact instance/pane identity and observed foreground/prompt readiness.

PowerShell's execution policy is preserved. Under AllSigned or Restricted,
unsigned prompt integration is skipped; PowerShell, tabs and splits still
work, but automatic prompt marks and prompt-based title updates are absent.

This development port preserves the RC6-era terminal engine and pane chrome.
Linux desktop integration is not part of the native application. Browser, media
and voice adapters are available through the optional WSL catalog; use
`kilix apps info NAME` for each adapter's prerequisites and supported operations.
Closing Kilix ends ordinary native PowerShell/cmd sessions. To preserve native
work, run `kilix session new work` (PowerShell) or
`kilix session new work -- cmd.exe` inside a native Kilix pane. It opens a
persistent tab. In a fresh Kilix window use `kilix session attach work` or
`kilix session restore`. `session list`, `detach` and `kill` manage live work.
The independent broker runs as this user, with private local IPC and bounded
64 MiB replay. No Windows service or scheduled task is installed.
To preserve WSL work,
install the current companion bundle, then run `kilix session new work` in WSL.
Close the pane/window or run `kilix session detach` inside that managed shell.
In a fresh WSL pane use `kilix session list`, `kilix session attach work`, or
`kilix session restore` (detached sessions in new tabs). `session kill work`
terminates the live processes. This reconnects real processes; it cannot bring
back work after reboot, WSL shutdown or broker termination. Ordinary panes are
unchanged. See wsl/README.txt for limits, replay and native control after attach.
WSL control uses a Windows loopback listener and a random per-instance password
passed through child environments. Managed WSL sessions also keep the current
attachment's control context in a private file for reconnection. Native
recovery metadata is under %LOCALAPPDATA%\Kilix\native-sessions; WSL metadata
is under /tmp/kilix-wsl-UID inside that distribution.
Unknown or missing passwords are denied.
Set `allow_remote_control no` in user configuration to disable WSL control.
The graphics pipe is local and restricted to the current Windows user and SYSTEM.

Source baseline and runtime hashes: baseline.json and manifest.json.
Licenses and native-port attribution: licenses\.
Source repositories:
  https://github.com/itsmygithubacct/kilix
  https://github.com/itsmygithubacct/kitty
  https://github.com/ecstra/win-kitty


Managed setup and updates:
  setup.ps1 -Archive ZIP -Sha256 VERIFIED_SHA256
  setup.ps1 -Action Wsl -WslDistribution Ubuntu -InstallWslDependencies
  setup.ps1 -Action Rollback
  setup.ps1 -Action Recover
  update.ps1 -Action Check -Channel stable
  update.ps1 -Action Apply -Channel preview
  diagnostics.ps1 -VerifyFiles -OutputPath diagnostics.json

Run these from PowerShell using .\ before the script name. WSL is optional;
-InstallWsl explicitly requests Microsoft WSL installation if needed, which can
require elevation/reboot and initial Linux account setup. The Ubuntu/Debian
-InstallWslDependencies option explicitly uses sudo for build/audio packages.

Managed installs use %LOCALAPPDATA%\Programs\Kilix by default. Use the same
-Destination for updates/rollback. Old generations and open windows are retained;
configuration, WSL models, receipts and user work are not removed. If optional
WSL setup fails, native Kilix remains usable; retry -Action Wsl separately.
Rollback changes only the native application, not WSL or user data.

Updates use published Windows releases and matching SHA256SUMS from this repo.
Drafts are ignored; no published release means no available update. Daily checks
are optional via update.ps1 -Action EnableSchedule; -ScheduleAction Apply opts
into automatic installation. Disable with -Action DisableSchedule. Nothing is
scheduled by default. Scheduled maintenance uses process-only -ExecutionPolicy
Bypass; persistent policies and terminal shell profiles are unchanged, and enforced
Group Policy still applies. The task follows the active generation after upgrades
or rollbacks. Re-enable older schedules once to adopt this dispatcher.

signing.json records actual signing status. Unsigned previews have no trusted
publisher signature. A checksum does not establish publisher certificate trust.
Integration tools test owned windows only; read windows/README.md in the matching
source archive for scope and manual checks. Windows11 is not claimed tested.
