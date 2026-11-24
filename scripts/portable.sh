# shellcheck shell=bash
# Host portability helpers for Kilix shell tools (GNU userland on Linux, BSD
# userland on FreeBSD). Source this file; it defines functions only.
#
# build.sh and kilix carry byte-identical copies of the block below, because
# both must work when copied on their own (the test suites run them that way).
# tests/test_portable_shell.py keeps the three copies identical: change this
# file, then copy the block into build.sh and kilix.
# --- Host portability -------------------------------------------------------
# GNU userland (Linux) and the BSD userland (FreeBSD) answer the same questions
# about files with different flags. Every call site below asks one of these
# functions a question instead of spelling one userland's flags, so each answer
# is chosen once, here. The external tools are still found on PATH by their
# plain names, so a test that shadows `mv` or `stat` shadows it here too.
# uname falls back to its absolute paths so a minimal PATH cannot quietly
# select the GNU spellings on a BSD host.
case "$(uname -s 2>/dev/null || /usr/bin/uname -s 2>/dev/null || /bin/uname -s 2>/dev/null || true)" in
  FreeBSD|DragonFly|NetBSD|OpenBSD|Darwin) _kilix_userland=bsd ;;
  *) _kilix_userland=gnu ;;
esac

# Owner uid of PATH (not following a final symlink).
kilix_stat_uid() {
  if [ "$_kilix_userland" = bsd ]; then stat -f '%u' -- "$1"; else stat -c '%u' -- "$1"; fi
}

# Permission bits of PATH in octal, as GNU `stat -c %a` prints them: no leading
# zero, special bits only when set (700, 600, 1777, 4755).
# With -L, of what a final symlink points to.
kilix_stat_mode() {
  local mode follow=()
  if [ "${1:-}" = -L ]; then follow=(-L); shift; fi
  if [ "$_kilix_userland" = bsd ]; then
    mode="$(stat "${follow[@]}" -f '%Mp%Lp' -- "$1")" || return 1
    case "$mode" in 0???) mode="${mode#0}" ;; esac
    printf '%s\n' "$mode"
  else
    stat "${follow[@]}" -c '%a' -- "$1"
  fi
}

# Size of PATH in bytes.
kilix_stat_size() {
  if [ "$_kilix_userland" = bsd ]; then stat -f '%z' -- "$1"; else stat -c '%s' -- "$1"; fi
}

# "<file type><TAB><device>:<inode>" of PATH, not following a final symlink.
# The type's spelling is the host stat's own; the value is only ever compared
# with another value taken on the same host.
kilix_stat_type_identity() {
  if [ "$_kilix_userland" = bsd ]; then
    stat -f '%HT%t%d:%i' -- "$1"
  else
    stat -c '%F\t%d:%i' -- "$1"
  fi
}

# uid:mode:link-count of PATH (not following a final symlink).
kilix_stat_meta() {
  local uid mode links
  if [ "$_kilix_userland" = bsd ]; then
    uid="$(stat -f '%u' -- "$1")" || return 1
    mode="$(kilix_stat_mode "$1")" || return 1
    links="$(stat -f '%l' -- "$1")" || return 1
    printf '%s:%s:%s\n' "$uid" "$mode" "$links"
  else
    stat -c '%u:%a:%h' -- "$1"
  fi
}

# device:inode of PATH; with -L, of what a final symlink points to.
kilix_stat_identity() {
  local follow=()
  if [ "${1:-}" = -L ]; then follow=(-L); shift; fi
  if [ "$_kilix_userland" = bsd ]; then
    stat "${follow[@]}" -f '%d:%i' -- "$1"
  else
    stat "${follow[@]}" -c '%d:%i' -- "$1"
  fi
}

# device:inode of the file open on descriptor FD of this shell. Linux exposes
# it as /proc/$$/fd/FD. FreeBSD has neither /proc nor (without fdescfs mounted)
# /dev/fd/N above 2, but base fstat(1) -n reports each descriptor's device (in
# hex) and inode; printf %u turns the device into stat's unsigned decimal.
kilix_fd_identity() {
  local fd="$1" device inode
  if [ "$_kilix_userland" = gnu ]; then
    stat -Lc '%d:%i' -- "/proc/$$/fd/$fd"
    return $?
  fi
  read -r device inode < <(fstat -n -p "$$" 2>/dev/null \
    | awk -v fd="$fd" '$4 == fd { print $5, $6; exit }') || true
  case "${device:-}" in 0x[0-9a-fA-F]*) ;; *) return 1 ;; esac
  case "${inode:-}" in ''|*[!0-9]*) return 1 ;; esac
  printf '%u:%s\n' "$device" "$inode"
}

# True when descriptor FD of this shell is open.
kilix_fd_is_open() {
  if [ "$_kilix_userland" = gnu ]; then
    [ -e "/proc/$$/fd/$1" ]
  else
    { true >&"$1"; } 2>/dev/null || { true <&"$1"; } 2>/dev/null
  fi
}

# The tool that takes an exclusive flock(2) on an open descriptor of this
# shell: util-linux flock(1) on Linux, base lockf(1) on FreeBSD (its `fd` form
# uses flock(2), so both interoperate with Python's fcntl.flock).
kilix_lock_tool() {
  if [ "$_kilix_userland" = bsd ]; then printf 'lockf\n'; else printf 'flock\n'; fi
}
kilix_lock_fd() {
  if [ "$_kilix_userland" = bsd ]; then lockf -s "$1"; else flock -x "$1"; fi
}
# The same lock, but fail at once instead of waiting when it is held.
kilix_lock_fd_try() {
  if [ "$_kilix_userland" = bsd ]; then lockf -s -t 0 "$1"; else flock -n "$1"; fi
}

# realpath -e: canonicalize PATH, failing unless every component exists. BSD
# realpath already fails on a missing path; GNU needs -e for the last one.
kilix_realpath_e() {
  if [ "$_kilix_userland" = bsd ]; then realpath -- "$1"; else realpath -e -- "$1"; fi
}

# realpath -m: canonicalize PATH whether or not it, or any parent, exists.
# Existing leading components are resolved physically; the rest lexically.
kilix_realpath_m() {
  local path="$1" resolved="" component
  local -a parts
  if [ "$_kilix_userland" = gnu ]; then
    realpath -m -- "$path"
    return $?
  fi
  case "$path" in /*) ;; *) path="$PWD/$path" ;; esac
  IFS=/ read -r -a parts <<<"$path"
  for component in "${parts[@]}"; do
    case "$component" in
      ''|.) continue ;;
      ..) resolved="${resolved%/*}"; continue ;;
    esac
    resolved="$resolved/$component"
    if [ -e "$resolved" ]; then
      resolved="$(realpath -- "$resolved")" || return 1
      [ "$resolved" != / ] || resolved=""
    fi
  done
  printf '%s\n' "${resolved:-/}"
}

# mv -T: rename SOURCE onto DEST itself, never into a directory DEST names. GNU
# mv -T refuses a real directory at DEST; BSD mv -h stops a symlink DEST from
# being followed, and the explicit check refuses the real directory.
kilix_mv_onto() {
  local _kilix_mv_flags=() source dest
  if [ "${1:-}" = -f ]; then _kilix_mv_flags=(-f); shift; fi
  source="$1" dest="$2"
  if [ "$_kilix_userland" = gnu ]; then
    mv -T "${_kilix_mv_flags[@]}" -- "$source" "$dest"
    return $?
  fi
  if [ -d "$dest" ] && [ ! -L "$dest" ]; then
    echo "mv: cannot overwrite directory '$dest' with '$source'" >&2
    return 1
  fi
  mv -h "${_kilix_mv_flags[@]}" -- "$source" "$dest"
}

# Lower-case SHA-256 hex digest of FILE.
kilix_sha256_tool() {
  if [ "$_kilix_userland" = bsd ]; then printf '/sbin/sha256\n'; else printf 'sha256sum\n'; fi
}
kilix_sha256() {
  local line
  if [ "$_kilix_userland" = bsd ]; then
    line="$(/sbin/sha256 -q -- "$1")" || return 1
  else
    line="$(sha256sum -- "$1")" || return 1
    line="${line%% *}"
  fi
  printf '%s\n' "${line,,}"
}

# Print the resolved executable path of every process this user can see.
kilix_process_executables() {
  local proc_exe
  if [ "$_kilix_userland" = bsd ]; then
    # PID COMM OSREL PATH; kernel threads report "-".
    procstat -h -b -a 2>/dev/null \
      | awk '{ if (sub(/^ *[0-9]+ +[^ ]+ +[0-9]+ +/, "") && $0 != "-") print }'
    return 0
  fi
  [ -d /proc ] || return 0
  for proc_exe in /proc/[0-9]*/exe; do
    [ -L "$proc_exe" ] || continue
    readlink -f -- "$proc_exe" 2>/dev/null || true
  done
}
# Bytes in sockaddr_un.sun_path, terminator included: 108 on Linux, 104 on the
# BSDs. A socket path must be shorter than this.
kilix_unix_socket_path_max() {
  if [ "$_kilix_userland" = bsd ]; then printf '104\n'; else printf '108\n'; fi
}
# Format a Unix time (seconds) with a strftime FORMAT (leading "+" included).
kilix_format_epoch() {
  if [ "$_kilix_userland" = bsd ]; then date -r "$1" "$2"; else date -d "@$1" "$2"; fi
}

# Copy one filesystem entry, never following a symlink, keeping its mode and
# times (GNU `cp -a --no-dereference`; BSD `cp -a` never dereferences).
kilix_copy_entry() {
  if [ "$_kilix_userland" = bsd ]; then
    cp -a -- "$1" "$2"
  else
    cp -a --no-dereference -- "$1" "$2"
  fi
}

# A token that names one incarnation of process PID: the same value for as long
# as that process lives, a different one if the PID is reused. Fails when PID is
# not running or is a zombie. Linux: the start tick, field 22 of /proc/PID/stat.
# FreeBSD: ps(1)'s start time as epoch seconds. Numeric on both hosts; compare
# only values read on the same host.
kilix_process_start_token() {
  local pid="$1" record state started
  local -a fields=()
  if [ "$_kilix_userland" = bsd ]; then
    record="$(LC_ALL=C ps -ww -o state= -o lstart= -p "$pid" 2>/dev/null)" || return 1
    read -r state started <<<"$record"
    [ -n "${started:-}" ] || return 1
    case "$state" in Z*) return 1 ;; esac
    LC_ALL=C date -j -f '%a %b %e %T %Y' "$started" +%s 2>/dev/null || return 1
    return 0
  fi
  [ -r "/proc/$pid/stat" ] || return 1
  record=$(<"/proc/$pid/stat") || return 1
  # comm can contain spaces and closing parentheses; fields follow the last ).
  read -r -a fields <<<"${record##*) }"
  [ "${fields[0]:-}" != Z ] && [ "${fields[0]:-}" != X ] || return 1
  [[ "${fields[19]:-}" =~ ^[0-9]+$ ]] || return 1
  printf '%s\n' "${fields[19]}"
}
# GNU make: `make` on Linux, the `gmake` package on FreeBSD (whose own make is
# bmake, which has neither GNU syntax nor GNU long options).
kilix_make_tool() {
  if [ "$_kilix_userland" = bsd ]; then printf 'gmake\n'; else printf 'make\n'; fi
}
kilix_make() {
  if [ "$_kilix_userland" = bsd ]; then gmake "$@"; else make "$@"; fi
}
# Copy FILE to DEST keeping its mode and times (GNU `--preserve=mode,timestamps`;
# BSD `cp -p`, which also tries owner and group and stays silent when it can't).
kilix_copy_mode_times() {
  if [ "$_kilix_userland" = bsd ]; then
    cp -p -- "$1" "$2"
  else
    cp --preserve=mode,timestamps -- "$1" "$2"
  fi
}

# Regular files directly in DIR matching NAME: "MTIME PATH" lines (fractional
# seconds, for sort -n), or just their sizes in bytes.
kilix_find_mtime_paths() {
  if [ "$_kilix_userland" = bsd ]; then
    find "$1" -maxdepth 1 -type f -name "$2" -exec stat -f '%Fm %N' -- {} +
  else
    find "$1" -maxdepth 1 -type f -name "$2" -printf '%T@ %p\n'
  fi
}
kilix_find_mtime_size_paths() {
  if [ "$_kilix_userland" = bsd ]; then
    find "$1" -maxdepth 1 -type f -name "$2" -exec stat -f '%Fm %z %N' -- {} +
  else
    find "$1" -maxdepth 1 -type f -name "$2" -printf '%T@ %s %p\n'
  fi
}
kilix_find_sizes() {
  if [ "$_kilix_userland" = bsd ]; then
    find "$1" -maxdepth 1 -type f -name "$2" -exec stat -f '%z' -- {} +
  else
    find "$1" -maxdepth 1 -type f -name "$2" -printf '%s\n'
  fi
}

# The host's own Python 3, outside any virtual environment: Debian's
# /usr/bin/python3, or the python3 package's /usr/local/bin/python3 on FreeBSD.
kilix_system_python() {
  if [ "$_kilix_userland" = bsd ]; then printf '/usr/local/bin/python3\n'; else printf '/usr/bin/python3\n'; fi
}
# ---------------------------------------------------------------------------
