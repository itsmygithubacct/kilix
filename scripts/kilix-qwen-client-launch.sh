#!/bin/sh
# Run only the pinned, dependency-free Qwen socket client from its own source.
set -eu
here=$(dirname -- "$(readlink -f -- "$0")")
library=$(dirname -- "$here")/lib
exec /usr/bin/python3 -I -c '
import sys
sys.path.insert(0, sys.argv[1])
from kilix_qwen_tts.cli import main
raise SystemExit(main(sys.argv[2:]))
' "$library" "$@"
