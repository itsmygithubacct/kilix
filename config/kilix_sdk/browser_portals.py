"""Select browser portal capture while keeping each window on private X11."""
from __future__ import annotations

import os
from pathlib import Path
import sys
from typing import Mapping, Sequence

FIREFOX = frozenset(("firefox", "firefox-esr"))
CHROMIUM = frozenset(("chromium", "chromium-browser", "google-chrome", "google-chrome-stable", "chrome"))


def prepare_browser(argv: Sequence[str], env: Mapping[str, str]) -> tuple[list[str], dict[str, str]]:
    command, environment = list(argv), dict(env)
    browser = Path(command[0]).name if command else ""
    if (browser not in FIREFOX | CHROMIUM or
            environment.get("KILIX_PRIVATE_XAPP") != "1" or
            not environment.get("KILIX_PORTAL_HOST_BUS")):
        return command, environment
    # Browser WebRTC gates its portal capturer on both of these values. They
    # select the capture protocol here; its GUI toolkit remains explicitly
    # X11. An absolute non-socket path cannot grant access to host Wayland.
    # The ordinary browser permission UI and desktop consent still apply.
    environment.update(XDG_SESSION_TYPE="wayland", WAYLAND_DISPLAY="/dev/null",
                       GDK_BACKEND="x11")
    environment.pop("WAYLAND_SOCKET", None)
    if browser in FIREFOX:
        environment["MOZ_ENABLE_WAYLAND"] = "0"
    else:
        # Ozone would otherwise select Wayland for the browser window, too.
        # Private app sessions supply X11. Preserve profiles, permission and
        # sandbox options, URLs and arguments after the literal separator.
        separator = command.index("--") if "--" in command else len(command)
        head = [arg for arg in command[1:separator]
                if arg != "--ozone-platform" and not arg.startswith("--ozone-platform=")]
        command = [command[0], *head, "--ozone-platform=x11", *command[separator:]]
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
