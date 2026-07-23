#!/usr/bin/env bash
# Install the exact tmux-tui + tmux-cli source closure selected by Kilix.
set -euo pipefail
umask 077

KILIX_HOME="${KILIX_HOME:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
GPU_TERMINAL_SOURCE_HOME="${GPU_TERMINAL_SOURCE_HOME:-$HOME/gpu_terminal}"
GPU_TERMINAL_HOME="${GPU_TERMINAL_HOME:-$HOME/.local/gpu_terminal}"
KILIX_STORAGE_HOME="${KILIX_STORAGE_HOME:-$GPU_TERMINAL_HOME/kilix}"
KILIX_STATE_DIRECTORY="${KILIX_STATE_DIRECTORY:-$KILIX_STORAGE_HOME/state}"
TMUX_TUI_PREFIX="${TMUX_TUI_PREFIX:-$HOME/.local}"

# This pair is one immutable source closure. tmux-tui vendors tmux-cli as a
# submodule; keeping both expected commits here makes a changed gitlink fail
# closed even when a caller overrides the outer repository.
TMUX_TUI_REPO="${TMUX_TUI_REPO:-https://github.com/itsmygithubacct/tmux-tui.git}"
TMUX_TUI_REF="${TMUX_TUI_REF:-a1ab67938b754adbb509a9f48c0e1c795421f4bf}"
TMUX_CLI_REF="${TMUX_CLI_REF:-51e9801a9c26211494231577eb24c9ed799252db}"

die() { printf 'kilix tmux: %s\n' "$*" >&2; exit 1; }
log() { printf 'kilix tmux: %s\n' "$*" >&2; }

usage() {
  cat <<'EOF'
usage: install-tmux-tui.sh [--force] [--with-tb|--without-tb] [--print-refs]

  --force       revalidate and republish the installed commands
  --with-tb     also publish tmux-cli's tb.py as the `tb` command
  --without-tb  remove a tb link previously managed by this installer
  --print-refs  print the immutable source closure without changing anything
EOF
}

force=0
tb_mode=preserve
while [ "$#" -gt 0 ]; do
  case "$1" in
    --force) force=1 ;;
    --with-tb) tb_mode=install ;;
    --without-tb) tb_mode=remove ;;
    --print-refs)
      printf '%s\n' \
        "tmux-tui=$TMUX_TUI_REF" \
        "tmux-cli=$TMUX_CLI_REF"
      exit 0 ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; exit 2 ;;
  esac
  shift
done

[ "$(id -u)" -ne 0 ] || die "run this installer as the desktop user, not root"
[[ "$TMUX_TUI_REF" =~ ^[0-9a-fA-F]{40}$ ]] \
  || die "TMUX_TUI_REF must be a full 40-character commit SHA"
[[ "$TMUX_CLI_REF" =~ ^[0-9a-fA-F]{40}$ ]] \
  || die "TMUX_CLI_REF must be a full 40-character commit SHA"

normalize_absolute() {
  local value="$1" normalized
  case "$value" in /*) ;; *) return 1 ;; esac
  normalized="$(realpath -m -- "$value" 2>/dev/null)" || return 1
  [ "$normalized" = "$value" ] || return 1
  printf '%s\n' "$normalized"
}

source_home="$(normalize_absolute "$GPU_TERMINAL_SOURCE_HOME")" \
  || die "GPU_TERMINAL_SOURCE_HOME must be a normalized absolute path"
prefix="$(normalize_absolute "$TMUX_TUI_PREFIX")" \
  || die "TMUX_TUI_PREFIX must be a normalized absolute path"
state_dir="$(normalize_absolute "$KILIX_STATE_DIRECTORY")" \
  || die "KILIX_STATE_DIRECTORY must be a normalized absolute path"
case "$source_home" in /|"$HOME") die "refusing broad source root: $source_home" ;; esac
case "$prefix" in /|"$HOME") die "refusing broad install prefix: $prefix" ;; esac

mkdir -p -- "$source_home" "$state_dir" "$prefix/bin"
chmod 0700 -- "$state_dir" 2>/dev/null || true
for protected in "$source_home" "$state_dir"; do
  [ -d "$protected" ] && [ ! -L "$protected" ] \
    && [ "$(stat -c '%u' -- "$protected" 2>/dev/null)" = "$(id -u)" ] \
    || die "source/state directories must be real directories owned by the current user: $protected"
done
if command -v flock >/dev/null 2>&1; then
  exec 9>"$state_dir/tmux-tui-install.lock"
  flock 9
fi

for command in git python3 ln readlink realpath; do
  command -v "$command" >/dev/null 2>&1 || die "$command is required"
done

managed_sources="$source_home/.tmux-tui-sources"
mkdir -p -- "$managed_sources"
chmod 0700 -- "$managed_sources" 2>/dev/null || true
[ -d "$managed_sources" ] && [ ! -L "$managed_sources" ] \
  && [ "$(stat -c '%u:%a' -- "$managed_sources" 2>/dev/null)" = "$(id -u):700" ] \
  || die "managed source directory must be owned by the current user with mode 0700"

checkout_dir="$managed_sources/tmux-tui-$TMUX_TUI_REF"
tmux_tui_bin="$checkout_dir/tmux_tui.py"
tb_bin="$checkout_dir/tmux-cli/tb.py"
tmux_tui_link="$prefix/bin/tmux-tui"
tb_link="$prefix/bin/tb"
stamp="$state_dir/tmux-tui-install.refs"
expected_refs="$(printf '%s\n' \
  "tmux-tui=$TMUX_TUI_REF" \
  "tmux-cli=$TMUX_CLI_REF")"

clone_tmp=""
cleanup() {
  [ -z "$clone_tmp" ] || rm -rf -- "$clone_tmp"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

ensure_checkout() {
  local origin head nested staged
  if git -C "$checkout_dir" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    origin="$(git -C "$checkout_dir" remote get-url origin 2>/dev/null || true)"
    [ "$origin" = "$TMUX_TUI_REPO" ] \
      || die "tmux-tui checkout has origin '${origin:-missing}', expected '$TMUX_TUI_REPO': $checkout_dir"
    [ -z "$(git -C "$checkout_dir" status --porcelain --untracked-files=normal)" ] \
      || die "tmux-tui checkout has local changes: $checkout_dir"
    head="$(git -C "$checkout_dir" rev-parse HEAD 2>/dev/null || true)"
    [ "${head,,}" = "${TMUX_TUI_REF,,}" ] \
      || die "tmux-tui checkout is at ${head:-unknown}, expected $TMUX_TUI_REF"
  else
    [ ! -e "$checkout_dir" ] \
      || die "tmux-tui path exists but is not a Git checkout: $checkout_dir"
    clone_tmp="$(mktemp -d "$managed_sources/.clone.XXXXXX")" \
      || die "could not allocate a temporary clone directory"
    staged="$clone_tmp/checkout"
    log "cloning pinned tmux-tui -> $checkout_dir"
    git clone --no-checkout -- "$TMUX_TUI_REPO" "$staged" \
      || die "could not clone tmux-tui from $TMUX_TUI_REPO"
    git -C "$staged" checkout --detach "$TMUX_TUI_REF" \
      || die "tmux-tui commit $TMUX_TUI_REF is unavailable"
    git -c protocol.file.allow=always -C "$staged" \
      submodule update --init --recursive \
      || die "could not initialize tmux-tui's tmux-cli submodule"
    mv -- "$staged" "$checkout_dir" || die "could not publish tmux-tui checkout"
    rm -rf -- "$clone_tmp"
    clone_tmp=""
  fi

  git -c protocol.file.allow=always -C "$checkout_dir" \
    submodule update --init --recursive \
    || die "could not reconcile tmux-tui's tmux-cli submodule"
  head="$(git -C "$checkout_dir" rev-parse HEAD 2>/dev/null || true)"
  nested="$(git -C "$checkout_dir/tmux-cli" rev-parse HEAD 2>/dev/null || true)"
  [ "${head,,}" = "${TMUX_TUI_REF,,}" ] \
    || die "tmux-tui resolved to the wrong commit"
  [ "${nested,,}" = "${TMUX_CLI_REF,,}" ] \
    || die "tmux-cli resolved to $nested, expected $TMUX_CLI_REF"
  [ -f "$tmux_tui_bin" ] && [ ! -L "$tmux_tui_bin" ] \
    || die "tmux-tui entrypoint is missing or unsafe"
  [ -f "$tb_bin" ] && [ ! -L "$tb_bin" ] \
    || die "tmux-cli entrypoint is missing or unsafe"
  chmod 0755 -- "$tmux_tui_bin" "$tb_bin"
  python3 "$tmux_tui_bin" --version >/dev/null \
    || die "tmux-tui version probe failed"
  python3 "$tb_bin" --version >/dev/null \
    || die "tmux-cli version probe failed"
}

managed_link() {
  local target="$1" link="$2" label="$3" current temporary
  if [ -e "$link" ] || [ -L "$link" ]; then
    [ -L "$link" ] \
      || die "refusing to replace non-symlink $label command: $link"
    current="$(readlink -f -- "$link" 2>/dev/null || true)"
    case "$current" in
      "$managed_sources"/*) ;;
      "$target") ;;
      *) die "refusing to replace unmanaged $label link: $link -> ${current:-broken}" ;;
    esac
  fi
  temporary="$prefix/bin/.$label.$$.tmp"
  [ ! -e "$temporary" ] && [ ! -L "$temporary" ] \
    || die "temporary link path already exists: $temporary"
  ln -s -- "$target" "$temporary"
  mv -fT -- "$temporary" "$link"
}

remove_managed_tb_link() {
  local current
  [ -e "$tb_link" ] || [ -L "$tb_link" ] || return 0
  [ -L "$tb_link" ] || die "refusing to remove non-symlink tb command: $tb_link"
  current="$(readlink -f -- "$tb_link" 2>/dev/null || true)"
  case "$current" in
    "$managed_sources"/*/tmux-cli/tb.py)
      rm -f -- "$tb_link"
      log "removed managed tb command alias" ;;
    *) die "refusing to remove unmanaged tb link: $tb_link -> ${current:-broken}" ;;
  esac
}

if [ "$force" = 0 ] && [ -f "$stamp" ] \
     && printf '%s\n' "$expected_refs" | cmp -s - "$stamp" \
     && [ -x "$tmux_tui_link" ]; then
  ensure_checkout
else
  ensure_checkout
fi

managed_link "$tmux_tui_bin" "$tmux_tui_link" tmux-tui
case "$tb_mode" in
  install) managed_link "$tb_bin" "$tb_link" tb ;;
  remove) remove_managed_tb_link ;;
esac

stamp_tmp="$(mktemp "$state_dir/.tmux-tui-refs.XXXXXX")" \
  || die "could not create install stamp"
printf '%s\n' "$expected_refs" >"$stamp_tmp"
chmod 0600 -- "$stamp_tmp"
mv -fT -- "$stamp_tmp" "$stamp"
log "installed and verified $tmux_tui_link"
[ "$tb_mode" != install ] || log "installed and verified $tb_link"
