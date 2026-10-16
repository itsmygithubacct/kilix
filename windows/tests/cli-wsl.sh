#!/bin/sh
set -eu
command -v kilix
output=$(kilix version)
case "$output" in *'Kilix 0.2.2 Windows development build'*) ;; *) exit 1 ;; esac
output=$(kilix help)
case "$output" in *'kilix engine --help'*) ;; *) exit 1 ;; esac
set +e
output=$(kilix needle --help 2>&1)
status=$?
set -e
test "$status" -eq 2
case "$output" in *'Unsupported Windows command: needle'*) ;; *) exit 1 ;; esac
output=$(kilix engine --help)
case "$output" in *'--config'*) ;; *) exit 1 ;; esac
echo 'PASS WSL command lookup, help, version, engine help, and error status'
