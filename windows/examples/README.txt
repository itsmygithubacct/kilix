Windows desktop automation examples

Open PowerShell in the extracted Kilix folder. The bundled Python can run the
Notepad example without installing Python separately:

  .\kitty\launcher\kitty-console.exe +runpy "import runpy,sys;sys.argv=['notepad.py'];runpy.run_path('examples/notepad.py',run_name='__main__')"

It creates a new note, types Unicode text, saves and checks the file, captures
the window, closes it, and prints the retained note and screenshot paths.
The example targets Windows 10 Notepad. The desktop must be logged in and
unlocked. The existing user's Notepad documents are not selected.

Discover commands:

  .\kilix-cli.exe desktop --help
  .\kilix-cli.exe desktop find --app notepad.exe --json
  .\kilix-cli.exe desktop screenshot --local .\desktop.png

On a Linux Kilix host with a configured SSH alias:

  kilix windows --host windows-workstation find --app notepad.exe --json
  kilix windows --host windows-workstation screenshot --local ./desktop.png

The local PNG is written on the computer running the command. In WSL, a Linux
path is written in that distribution; native Windows paths still work.
Use existing SSH configuration for a jump host. No host-specific route is
embedded in Kilix. Prefer UIA names/automation IDs and condition waits when
writing scripts; use desktop COMMAND --help for each command's options.
