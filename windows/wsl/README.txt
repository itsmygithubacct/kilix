WSL companion setup

Pane/tab commands need Python 3, WSL interop and Windows PATH import. Open a
fresh Kilix WSL window after installing the native package. Run `kilix help`.

Needle and Pong additionally need a per-user Linux installation. On Ubuntu:
  sudo apt-get install build-essential zlib1g-dev pulseaudio-utils
pulseaudio-utils provides the audio client for WSLg sound. Graphics use inline
transfers because Windows cannot access Linux shared-memory buffers.
Use a trusted Linux uv executable to provide Python 3.12.14. Nothing is installed
in the Windows Python environment.

Create the companion source bundle from initialized source checkouts on Linux:
  python3 bundle.py --needle /path/to/kilix-needle --games /path/to/kilix-games \
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
and a private Python interpreter. Existing generations remain available. Model
license receipts use the standard ~/.local/gpu_terminal/license-receipts store.

Examples:
  kilix needle "split right"
  kilix needle apps "open pong"
  kilix --launch kilix-pong
  kilix pane list
  kilix pane right -- bash
  kilix native help

Linux app argv and the invoking Linux cwd are passed through wsl.exe. Raw launch
options that have no supported translation fail explicitly. Prompt-sensitive
Needle typing still requires shell integration. Desktop services and the full
Linux app installer are outside this companion's current scope.
