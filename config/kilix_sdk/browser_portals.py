"""Select Firefox's portal capture path while keeping its window on private X11."""
from __future__ import annotations

import os
from pathlib import Path
import sys
from typing import Mapping, Sequence


def prepare_browser(argv: Sequence[str], env: Mapping[str, str]) -> tuple[list[str], dict[str, str]]:
    command, environment = list(argv), dict(env)
    if (not command or Path(command[0]).name not in {"firefox", "firefox-esr"} or
            environment.get("KILIX_PRIVATE_XAPP") != "1" or
            not environment.get("KILIX_PORTAL_HOST_BUS")):
        return command, environment
    # Firefox/WebRTC gates its portal capturer on both of these values. They
    # select the capture protocol here; its GUI toolkit remains explicitly
    # X11. An absolute non-socket path cannot grant access to host Wayland.
    # The ordinary browser permission UI and desktop consent still apply.
    environment.update(XDG_SESSION_TYPE="wayland", WAYLAND_DISPLAY="/dev/null",
                       GDK_BACKEND="x11", MOZ_ENABLE_WAYLAND="0")
    environment.pop("WAYLAND_SOCKET", None)
    return command, environment


def main() -> None:
    args = sys.argv[1:]
    if args[:1] == ["--"]:
        args = args[1:]
    if not args:
        raise SystemExit("browser_portals.py requires a browser command")
    command, environment = prepare_browser(args, os.environ)
    os.execvpe(command[0], command, environment)


if __name__ == "__main__":
    main()
