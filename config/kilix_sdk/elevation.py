"""Start the installed GParted through setuid sudo.

The private desktop bus sets no_new_privs before an app runs, so pkexec
inside that bus cannot become root. This module builds the sudo command
while the launcher can still execute a setuid program. The password is not
placed on the command line or in the environment. kilix_askpass collects it
and writes it only on sudo's pipe, and only when that parent is the setuid
sudo binary running this same command.
"""

import os
import stat


SUDO_PATHS = frozenset({"/usr/bin/sudo", "/bin/sudo"})
ENV_PATH = "/usr/bin/env"
GPARTED_DIRECTORIES = frozenset({"/usr/sbin", "/usr/bin"})
ASKPASS_NAME = "kilix_askpass"
SAFE_PATH = "/usr/bin:/bin"

# A caller-controlled loader or toolkit path can replace the password dialog.
_DROPPED_ENV = (
    "LD_PRELOAD", "LD_PRELOAD_32", "LD_PRELOAD_64", "LD_LIBRARY_PATH",
    "LD_AUDIT", "LD_PROFILE", "LD_ORIGIN_PATH", "LD_DEBUG", "LD_DEBUG_OUTPUT",
    "PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP", "PYTHONINSPECT",
    "PYTHONWARNINGS", "PYTHONEXECUTABLE", "PYTHONNOUSERSITE", "PYTHONSAFEPATH",
    "GTK_MODULES", "GTK_PATH", "GTK_IM_MODULE", "GTK_IM_MODULE_FILE",
    "GDK_PIXBUF_MODULE_FILE", "GDK_PIXBUF_MODULEDIR",
    "GIO_MODULE_DIR", "GIO_EXTRA_MODULES",
    "BASH_ENV", "ENV", "SHELLOPTS", "PS4", "PERL5OPT", "RUBYOPT", "NODE_OPTIONS",
)


class ElevationRefused(RuntimeError):
    """GParted was requested, but it cannot be started safely."""


class ElevationPlan:
    """argv and environment for execve. Neither carries the password."""

    def __init__(self, argv, env):
        self.argv = tuple(argv)
        self.env = env


def askpass_path(config_dir=None):
    if config_dir is None:
        config_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(config_dir, ASKPASS_NAME)


def _ancestors(path):
    items = []
    current = path
    while True:
        items.append(current)
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    return items


def _plain(value):
    return isinstance(value, str) and value != "" and "\n" not in value and "\x00" not in value


def trusted_root_program(path, *, stat_fn, lstat_fn, realpath_fn,
                         basename=None, directories=None, require_setuid=False):
    """Return the real path of a root-owned program, or None.

    Every path component of the resolved file must be root-owned and not
    writable by group or other. A symlink component fails closed because
    lstat is used after realpath resolves the final file.
    """
    if not _plain(path) or not os.path.isabs(path):
        return None
    try:
        real = realpath_fn(path)
    except OSError:
        return None
    if not _plain(real) or not os.path.isabs(real):
        return None
    if basename is not None and os.path.basename(real) != basename:
        return None
    if directories is not None and os.path.dirname(real) not in directories:
        return None
    try:
        infos = [(item, lstat_fn(item)) for item in _ancestors(real)]
    except OSError:
        return None
    leaf = infos[0][1]
    if not stat.S_ISREG(leaf.st_mode) or leaf.st_uid != 0 or leaf.st_mode & 0o022:
        return None
    if require_setuid and not leaf.st_mode & stat.S_ISUID:
        return None
    for _item, info in infos[1:]:
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            return None
    try:
        stat_fn(real)
    except OSError:
        return None
    return real


def _display_token(environ):
    display = environ.get("DISPLAY")
    if not _plain(display) or any(ch.isspace() or ch == "=" for ch in display):
        return None
    return "DISPLAY=" + display


def _xauthority_token(environ, *, lstat_fn, realpath_fn):
    raw = environ.get("XAUTHORITY")
    if raw is None or raw == "":
        home = environ.get("HOME")
        if not _plain(home) or not os.path.isabs(home):
            return None
        raw = os.path.join(home, ".Xauthority")
    if not _plain(raw) or not os.path.isabs(raw):
        return None
    try:
        real = realpath_fn(raw)
        info = lstat_fn(real)
    except OSError:
        return None
    if not _plain(real) or not os.path.isabs(real) or not stat.S_ISREG(info.st_mode):
        return None
    if info.st_mode & 0o022:
        return None
    return "XAUTHORITY=" + real


def _trusted_askpass(path, *, lstat_fn):
    if not _plain(path) or not os.path.isabs(path) or os.path.basename(path) != ASKPASS_NAME:
        return None
    directory = os.path.dirname(path)
    try:
        program = lstat_fn(path)
        parent = lstat_fn(directory)
    except OSError:
        return None
    if not stat.S_ISREG(program.st_mode) or stat.S_ISLNK(program.st_mode):
        return None
    if program.st_mode & 0o022 or not program.st_mode & stat.S_IXUSR:
        return None
    if not stat.S_ISDIR(parent.st_mode) or parent.st_mode & 0o022:
        return None
    return path


def plan_gparted_elevation(argv, environ, *, stat_fn=os.stat, lstat_fn=os.lstat,
                           realpath_fn=os.path.realpath, config_dir=None):
    """Plan sudo for an installed gparted command.

    Return None when argv is not a gparted launch. Raise ElevationRefused
    when it is gparted but sudo, the display, or the program itself is not
    safe to use. Never fall through into the private bus for that command.
    """
    if not argv or not _plain(argv[0]) or os.path.basename(argv[0]) != "gparted":
        return None
    for arg in argv[1:]:
        if not _plain(arg):
            raise ElevationRefused("GParted arguments must be plain text")
    checks = dict(stat_fn=stat_fn, lstat_fn=lstat_fn, realpath_fn=realpath_fn)
    gparted = trusted_root_program(
        argv[0], basename="gparted", directories=GPARTED_DIRECTORIES, **checks)
    if gparted is None:
        raise ElevationRefused("GParted is not the installed root-owned program")
    display = _display_token(environ)
    if display is None:
        raise ElevationRefused("GParted needs a graphical session")
    xauthority = _xauthority_token(environ, lstat_fn=lstat_fn, realpath_fn=realpath_fn)
    if xauthority is None:
        raise ElevationRefused("GParted needs the session's X authority file")
    sudo = None
    for candidate in ("/usr/bin/sudo", "/bin/sudo"):
        found = trusted_root_program(
            candidate, basename="sudo", require_setuid=True, **checks)
        if found in SUDO_PATHS:
            sudo = found
            break
    if sudo is None:
        raise ElevationRefused("GParted needs setuid sudo")
    env_bin = trusted_root_program(ENV_PATH, basename="env", **checks)
    if env_bin != ENV_PATH:
        raise ElevationRefused("GParted needs root-owned /usr/bin/env")
    askpass = _trusted_askpass(askpass_path(config_dir), lstat_fn=lstat_fn)
    if askpass is None:
        raise ElevationRefused("GParted's password dialog is not available")
    prompt = f"Authentication is required to run {gparted} as root."
    command = (sudo, "--askpass", "--", env_bin, display, xauthority, gparted, *argv[1:])
    env = dict(environ)
    for key in _DROPPED_ENV:
        env.pop(key, None)
    env["PATH"] = SAFE_PATH
    env["SUDO_ASKPASS"] = askpass
    env["SUDO_PROMPT"] = prompt
    env["DISPLAY"] = display.split("=", 1)[1]
    env["XAUTHORITY"] = xauthority.split("=", 1)[1]
    return ElevationPlan(command, env)


def command_after_sudo(argv):
    """Return the command after the exact sudo shape this module launches.

    Any other sudo option, including a shell or a login, is refused.
    """
    if not isinstance(argv, (list, tuple)) or len(argv) < 4:
        return None
    if not _plain(argv[0]) or os.path.basename(argv[0]) != "sudo":
        return None
    if argv[1] != "--askpass" or argv[2] != "--":
        return None
    if any(not _plain(item) for item in argv):
        return None
    return list(argv[3:])


def gparted_target(command, *, stat_fn, lstat_fn, realpath_fn):
    """Return the trusted gparted path wrapped by /usr/bin/env, or None."""
    if not isinstance(command, (list, tuple)) or len(command) < 4:
        return None
    if any(not _plain(item) for item in command):
        return None
    checks = dict(stat_fn=stat_fn, lstat_fn=lstat_fn, realpath_fn=realpath_fn)
    if trusted_root_program(command[0], basename="env", **checks) != ENV_PATH:
        return None
    if command[0] != ENV_PATH or not command[1].startswith("DISPLAY="):
        return None
    display = command[1][len("DISPLAY="):]
    if not _plain(display) or any(ch.isspace() or ch == "=" for ch in display):
        return None
    if not command[2].startswith("XAUTHORITY=/"):
        return None
    xauthority = command[2][len("XAUTHORITY="):]
    if not _plain(xauthority) or not os.path.isabs(xauthority):
        return None
    return trusted_root_program(
        command[3], basename="gparted", directories=GPARTED_DIRECTORIES, **checks)


def trusted_sudo_target(parent, *, stat_fn=os.stat, lstat_fn=os.lstat,
                        realpath_fn=os.path.realpath):
    """Return the gparted path when parent is setuid sudo running that plan.

    A helper that is not setuid can be executed by any program. The password
    is released only to the real setuid sudo binary, and only for the GParted
    command this module constructs.
    """
    if not isinstance(parent, dict):
        return None
    mode = parent.get("mode")
    uid = parent.get("uid")
    if not isinstance(mode, int) or not isinstance(uid, int):
        return None
    if uid != 0 or not stat.S_ISREG(mode) or not mode & stat.S_ISUID:
        return None
    exe = trusted_root_program(
        parent.get("exe"), stat_fn=stat_fn, lstat_fn=lstat_fn, realpath_fn=realpath_fn,
        basename="sudo", require_setuid=True)
    if exe not in SUDO_PATHS:
        return None
    command = command_after_sudo(parent.get("argv"))
    if command is None:
        return None
    return gparted_target(command, stat_fn=stat_fn, lstat_fn=lstat_fn, realpath_fn=realpath_fn)
