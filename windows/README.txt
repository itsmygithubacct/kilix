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
The application does not install or enable an SSH server or a WSL distribution.
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

PowerShell's execution policy is preserved. Under AllSigned or Restricted,
unsigned prompt integration is skipped; PowerShell, tabs and splits still
work, but automatic prompt marks and prompt-based title updates are absent.

This development port preserves the RC6-era terminal engine and pane chrome.
Linux desktop integration, in-pane browser, voice tools, and persistent PTY
broker/recovery are unavailable on Windows. Closing Kilix ends its Windows
child sessions. WSL background jobs retain WSL's own lifetime rules.
WSL control uses a Windows loopback listener and a random per-instance password
passed only through child environments. Unknown or missing passwords are denied.
Set `allow_remote_control no` in user configuration to disable WSL control.
The graphics pipe is local and restricted to the current Windows user and SYSTEM.

Source baseline and runtime hashes: baseline.json and manifest.json.
Licenses and native-port attribution: licenses\.
Source repositories:
  https://github.com/itsmygithubacct/kilix
  https://github.com/itsmygithubacct/kitty
  https://github.com/ecstra/win-kitty
