WSL companion setup

Pane/tab commands need Python 3, WSL interop and Windows PATH import. Setup uses
uv's managed Python 3.12.14 for archive extraction and building. Open a
fresh Kilix WSL window after installing the native package. Run `kilix help`.

Needle and the public game catalog need a per-user Linux installation. On Ubuntu:
  sudo apt-get install build-essential zlib1g-dev pulseaudio-utils
pulseaudio-utils provides the audio client for WSLg sound. Graphics use inline
transfers because Windows cannot access Linux shared-memory buffers.
Use a trusted Linux uv executable to provide Python 3.12.14. Nothing is installed
in the Windows Python environment.

Create the companion source bundle from initialized source checkouts on Linux:
  python3 bundle.py --needle /path/to/kilix-needle --games /path/to/kilix-games \
    --super-kilix /path/to/super-kilix --kilix-land /path/to/kilix-land \
    --plant-grower /path/to/pleb-plant-grower \
    --output companion.tar.gz

The exporter uses source-revisions.json, including each nested dependency pin;
uncommitted changes are excluded. It prints the SHA256. Transfer the bundle and
uv to the target machine, then run in the intended WSL distribution/user:
  sh /path/to/native-install/wsl/setup.sh /path/to/companion.tar.gz SHA256 /path/to/uv

Needle's model has its own license and verification. If the same owner already
has an accepted installation, bundle.py can import it with:
  --needle-model /path/to/content/assets/needle2 --receipt /path/to/existing-receipt.json
This includes the owner's acceptance record: keep that personal bundle private.
Without an imported model, run Needle interactively in Kilix to complete its
normal model/license setup. The companion never creates acceptance records.
Agent mode fails if model readiness or existing license coverage is missing.

Installation lives in ~/.local/share/kilix-wsl, with versioned source generations
and a private Python interpreter. Setup builds and checks the new generation
before switching the current symlink atomically; failed builds leave the previous
installation active. Existing generations remain available. Models are retained
across source-only upgrades; conflicting imported model data is rejected. Model
license receipts use the standard ~/.local/gpu_terminal/license-receipts store.

All fifteen games in game-catalog.json are installed: Solitaire, Lander,
Joustix, Brokeout, Bashed Earth, Lights, Jpak, Pong, Chess Bash, Rancher,
Fishtank, Tic-tac-toe, Super Kilix, Plant Grower, and Kilix Land. The twelve
small games come from kilix-games; the other three retain their own pinned
repositories and SDK dependencies. Sources, assets and license notices remain
in each generation. Launchers use the game's source directory so assets resolve
from any calling directory; relative file arguments are relative to that game.
User saves remain in the game's normal Linux user directories.

`kilix games list` (or `kilix apps`) lists installed canonical names, with
`--json` for scripts. `kilix games play NAME [ARGS]` runs in the current pane;
`kilix --launch NAME [ARGS]` opens a new tab. Both accept catalog aliases, such
as pong, chess, solitaire, lander, and plant-grower. Original binary names
terminal-lander and kitty-brokeout also work. Needle can launch the games its
apps vocabulary recognizes and uses this same installed-game readiness check.
Games outside this catalog and the full Linux app installer are not included.
Lights and Fishtank support Q to quit. Plant Grower's current upstream input
map has no quit key; close its Kilix pane when finished.

Examples:
  kilix needle "split right"
  kilix needle apps "open pong"
  kilix --launch kilix-pong
  kilix games list
  kilix --launch chess
  kilix games play solitaire --seed 7
  kilix pane list
  kilix pane right -- bash
  kilix native help

Linux app argv, Linux user and the invoking cwd are passed through wsl.exe.
Remote launch --env NAME=value sets a Linux variable; NAME= sets it empty and
NAME removes it. Values stay literal, including spaces, quotes and dollar signs.
Use `kilix @ --help` or `kilix @ launch --help` for remote command help.
Pane/tab help and launch help after options work without a live connection.
`kilix fullscreen` toggles the native window containing the invoking WSL pane.
Raw launch
options that have no supported translation fail explicitly. Prompt-sensitive
Needle typing still requires shell integration. Desktop services and the full
Linux app installer are outside this companion's current scope.
