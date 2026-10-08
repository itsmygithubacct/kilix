#!/bin/sh
# Install only the pinned native library; speech weights belong to Kilix Content.
exec python3 "$(dirname "$0")/install-kilix-whistle.py" "$@"
