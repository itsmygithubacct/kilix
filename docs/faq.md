# Troubleshooting and FAQ

## Troubleshooting

- **No buttons / plain title bars?** You're on the prebuilt fallback, not the fork.
  Run `kilix --which` — if it prints a `…/kitty.app/bin/kitty` path, install the build
  deps (see [Requirements](install.md#requirements)) and run `kilix --build`, then relaunch.
- **Taskbar shows kitty's icon, not the flame?** Run `kilix --install-desktop`, then log
  out/in or restart your panel. On **Wayland** the icon comes only from the installed
  `.desktop`, so `--install-desktop` is required there.
- **`kilix` exits with no window / over SSH?** It's a GUI terminal and needs a local
  graphical session (`$DISPLAY` / `$WAYLAND_DISPLAY`); it won't run headless.
- **First run spews a compile and fails?** That's the fork build failing for lack of
  deps — Kilix then attempts the prebuilt fallback. Supply a pinned version and
  SHA-256 to `bootstrap.sh` to skip the build attempt entirely.

## FAQ

- **Why a fork of kitty?** Stock kitty can't put clickable buttons in its window chrome,
  so kilix ships a fork (the `./src` submodule) that wires title-bar clicks to kitty
  actions. It's a full fork kilix evolves freely — the buttons plus quality-of-life fixes.
- **Does it touch my normal kitty?** No. kilix runs its own binary, its own XDG
  config directory, and its own `--class`, so your system kitty
  and `~/.config/kitty` are untouched.
- **Does it work on Wayland?** Yes — splits, buttons, and keybindings all work; only the
  icon mechanism differs (see [Taskbar identity & icon](install.md#taskbar-identity--icon)).
- **Performance?** It's kitty — GPU-rendered, same speed. The buttons are drawn into the
  existing title-bar cells, so there's no extra overhead.
- **Windows/macOS?** No — Linux only (see [Requirements](install.md#requirements)).
