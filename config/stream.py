"""kilix — streaming supervisor: shared plumbing for the pixel-plane serve modes.

Used by `kilix run --serve` (Phase 2) and `kilix desktop` (Phase 3). Provides:
  - a private per-session runtime dir (0700) for sockets, pidfiles, secrets, logs
  - X display-number allocation held with an flock (TightVNC Xvnc has no
    -displayfd, so the server can't pick its own number)
  - Xvnc / Xvfb launch + readiness wait
  - a two-entry VncAuth password file (full control + view-only)
  - a bearer token and a first-use self-signed TLS cert (for the --lan bridge)
  - an HLS (H.264) broadcaster off an X display
  - connect-instruction printing (SSH-tunnel first)
  - teardown of every child on exit (atexit + signals)

Everything binds loopback by default; nothing here ever passes a non-loopback
interface to Xvnc, ffmpeg, or a socket. LAN exposure happens only through the
token+TLS wsbridge (config/wsbridge.py), never these primitives.
"""
import atexit
import fcntl
import glob
import os
import secrets
import shutil
import signal
import socket
import subprocess
import time


def kill_all():
    """Reap every kilix stream process (Xvnc/Xvfb/ffmpeg/bridge) recorded in the
    runtime dirs' pidfiles, and remove the session dirs. Backstop for a serve
    that was SIGKILLed (bypassing its own atexit/SIGTERM cleanup)."""
    base = os.environ.get("XDG_RUNTIME_DIR") or os.environ.get("TMPDIR") or "/tmp"
    root = os.path.join(base, "kilix")
    if not os.path.isdir(root):
        return 0
    killed = 0
    for name in os.listdir(root):
        d = os.path.join(root, name)
        if name == "locks" or not os.path.isdir(d):
            continue
        for pf in glob.glob(os.path.join(d, "*.pid")):
            try:
                pid = int(open(pf).read().strip())
                os.kill(pid, signal.SIGTERM)
                killed += 1
            except (OSError, ValueError):
                pass
        shutil.rmtree(d, ignore_errors=True)
    return killed


def _data_home():
    return os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share"))


def _whoami():
    import getpass
    try:
        return getpass.getuser()
    except Exception:
        return os.environ.get("USER", "user")


def _hostname():
    try:
        return socket.gethostname() or "HOST"
    except Exception:
        return "HOST"


def find_xvfb():
    p = shutil.which("Xvfb")
    if p:
        return p
    p = os.path.join(_data_home(), "kilix", "xvfb", "usr", "bin", "Xvfb")
    return p if os.access(p, os.X_OK) else None


def find_xvnc():
    # TightVNC ships `Xvnc`; TigerVNC (Debian's tigervnc-standalone-server) ships
    # `Xtigervnc` (and only sets up the `Xvnc` alternative on a system install).
    return shutil.which("Xvnc") or shutil.which("Xtigervnc")


def _xvnc_is_tiger(xvnc):
    # Binary name is the reliable signal (Debian ships TigerVNC as `Xtigervnc`);
    # fall back to the -version banner for an `Xvnc`-named TigerVNC.
    if "tiger" in os.path.basename(xvnc or "").lower():
        return True
    try:
        r = subprocess.run([xvnc, "-version"], capture_output=True, text=True, timeout=5)
        return "tigervnc" in (r.stdout + r.stderr).lower()
    except Exception:
        return False


def _fontpath_args():
    # When fonts are unpacked into a no-sudo prefix (install-stream-deps.sh sets
    # KILIX_XFONTS) the X server's built-in default font path misses them, so it
    # can't open 'fixed' and refuses to start. Point it at the unpacked dirs.
    fp = os.environ.get("KILIX_XFONTS")
    return ["-fp", fp] if fp else []


def free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
    finally:
        s.close()


def port_free(p):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", p))
        return True
    except OSError:
        return False
    finally:
        s.close()


def lan_ip():
    """Best-effort primary non-loopback IPv4, for printing a --lan URL."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("192.168.255.255", 1))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return _hostname()


class StreamSupervisor:
    def __init__(self, session):
        self.session = session
        base = (os.environ.get("XDG_RUNTIME_DIR")
                or os.environ.get("TMPDIR") or "/tmp")
        self.runtime_dir = os.path.join(base, "kilix", session)
        os.makedirs(self.runtime_dir, mode=0o700, exist_ok=True)
        # Display-number locks live in a SHARED dir (not the per-session runtime
        # dir), so the flock actually excludes OTHER concurrent serves from
        # picking the same X display before its socket appears.
        self.lockdir = os.path.join(base, "kilix", "locks")
        os.makedirs(self.lockdir, mode=0o700, exist_ok=True)
        try:
            os.chmod(self.runtime_dir, 0o700)
        except OSError:
            pass
        self.children = []     # list of (name, Popen)
        self._locks = []       # held flock fds reserving display numbers
        self._cleaned = False
        self.xauth = None      # per-session X authority file (MIT cookie)
        atexit.register(self.cleanup)

    # ---- process bookkeeping ------------------------------------------------
    def spawn(self, name, argv, **kw):
        p = subprocess.Popen(argv, **kw)
        self.children.append((name, p))
        try:
            with open(os.path.join(self.runtime_dir, f"{name}.pid"), "w") as f:
                f.write(str(p.pid))
        except OSError:
            pass
        return p

    def cleanup(self, *_):
        if self._cleaned:
            return
        self._cleaned = True
        for _name, p in reversed(self.children):
            if p.poll() is None:
                try:
                    p.terminate()
                except Exception:
                    pass
        deadline = time.time() + 3
        for _name, p in reversed(self.children):
            try:
                p.wait(timeout=max(0.0, deadline - time.time()))
            except Exception:
                try:
                    p.kill()
                except Exception:
                    pass
        for fd in self._locks:
            try:
                os.close(fd)
            except OSError:
                pass
        self._locks.clear()

    # ---- display allocation -------------------------------------------------
    def pick_display(self, lo=60, hi=120):
        for n in range(lo, hi):
            if os.path.exists(f"/tmp/.X11-unix/X{n}"):
                continue
            fd = os.open(os.path.join(self.lockdir, f"X{n}.lock"),
                         os.O_CREAT | os.O_RDWR, 0o600)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                os.close(fd)
                continue
            if os.path.exists(f"/tmp/.X11-unix/X{n}"):   # raced, appeared
                os.close(fd)
                continue
            self._locks.append(fd)
            return n
        raise RuntimeError("kilix: no free X display in range")

    def _wait_x(self, n, timeout=10):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if os.path.exists(f"/tmp/.X11-unix/X{n}"):
                time.sleep(0.2)      # let it start accepting
                return True
            time.sleep(0.1)
        return False

    def make_xauth(self, n):
        """Per-session MIT-MAGIC-COOKIE-1 X-authority file (0600) for display :n.
        Without it, the display's world-accessible /tmp/.X11-unix/X<n> socket lets
        ANY local user connect (screenshot/keylog/inject). With -auth on the server
        and XAUTHORITY set on the clients we launch, only our processes get in."""
        path = os.path.join(self.runtime_dir, f"Xauth-{n}")
        open(path, "a").close()
        os.chmod(path, 0o600)
        subprocess.run(["xauth", "-f", path, "add", f":{n}",
                        "MIT-MAGIC-COOKIE-1", secrets.token_hex(16)],
                       check=True, capture_output=True)
        self.xauth = path
        return path

    # ---- X servers ----------------------------------------------------------
    def start_xvnc(self, n, w, h, port, pwfile, desktop="kilix"):
        xvnc = find_xvnc()
        if not xvnc:
            raise RuntimeError("kilix: Xvnc not found (needed for --serve)")
        auth = self.make_xauth(n)
        argv = [xvnc, f":{n}", "-geometry", f"{w}x{h}", "-depth", "24",
                "-rfbport", str(port), "-rfbauth", pwfile, "-auth", auth,
                "-localhost", "-desktop", desktop, "-nolisten", "tcp"] + _fontpath_args()
        if _xvnc_is_tiger(xvnc):
            # TigerVNC must be told to actually offer VncAuth (its default set
            # differs); TightVNC has no -SecurityTypes and would reject it.
            argv += ["-SecurityTypes", "VncAuth"]
        logf = open(os.path.join(self.runtime_dir, f"xvnc-{n}.log"), "wb")
        p = self.spawn(f"xvnc-{n}", argv, stdout=logf, stderr=logf)
        if not self._wait_x(n):
            raise RuntimeError("kilix: Xvnc did not come up (see runtime log)")
        return p

    def start_xvfb(self, n, w, h):
        xvfb = find_xvfb()
        if not xvfb:
            raise RuntimeError("kilix: Xvfb not found")
        auth = self.make_xauth(n)
        argv = [xvfb, f":{n}", "-screen", "0", f"{w}x{h}x24",
                "-nolisten", "tcp", "-auth", auth] + _fontpath_args()
        logf = open(os.path.join(self.runtime_dir, f"xvfb-{n}.log"), "wb")
        p = self.spawn(f"xvfb-{n}", argv, stdout=logf, stderr=logf)
        if not self._wait_x(n):
            raise RuntimeError("kilix: Xvfb did not come up")
        return p

    # ---- broadcast (HLS, H.264) --------------------------------------------
    def start_hls(self, n, w, h, outdir, fps=15):
        os.makedirs(outdir, exist_ok=True)
        m3u8 = os.path.join(outdir, "live.m3u8")
        argv = ["ffmpeg", "-hide_banner", "-loglevel", "error",
                "-f", "x11grab", "-framerate", str(fps),
                "-video_size", f"{w}x{h}", "-i", f":{n}",
                "-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency",
                "-g", str(fps * 2), "-pix_fmt", "yuv420p",
                "-f", "hls", "-hls_time", "1", "-hls_list_size", "4",
                "-hls_flags", "delete_segments+omit_endlist", m3u8]
        logf = open(os.path.join(self.runtime_dir, f"hls-{n}.log"), "wb")
        return self.spawn(f"hls-{n}", argv, stdout=logf, stderr=logf)

    # ---- secrets ------------------------------------------------------------
    # Classic vncpasswd obfuscation key: the stored VNC password is the 8-byte
    # password DES-encrypted with this fixed key (d3des bit-reverses key bytes).
    _VNC_FIXED = bytes([23, 82, 107, 6, 35, 78, 88, 7])

    def _vnc_obfuscate(self, pw):
        p8 = pw.encode()[:8].ljust(8, b"\x00")
        key = bytes(int(f"{b:08b}"[::-1], 2) for b in self._VNC_FIXED).hex()
        for extra in (["-provider", "legacy", "-provider", "default"], []):
            r = subprocess.run(["openssl", "enc", "-des-ecb", "-e", "-K", key,
                                "-nopad"] + extra, input=p8, capture_output=True)
            if len(r.stdout) >= 8:
                return r.stdout[:8]
        raise RuntimeError("kilix: openssl des-ecb unavailable for VNC password")

    def make_vncpw(self, full, view):
        """Two-entry VNC passwd file (0600): entry 1 = full control, entry 2 =
        view-only. Generated with openssl — byte-identical to `vncpasswd` — so no
        vncpasswd binary is needed; works on TightVNC and TigerVNC hosts alike.
        A client authenticating with the `view` password gets a server-enforced
        view-only session."""
        data = self._vnc_obfuscate(full) + self._vnc_obfuscate(view)
        path = os.path.join(self.runtime_dir, "vncpw")
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        return path

    def mint_token(self):
        return secrets.token_urlsafe(32)

    def tls_cert(self):
        """First-use self-signed cert for the --lan HTTPS bridge.
        Returns (cert_path, key_path, sha256_fingerprint)."""
        d = os.path.join(_data_home(), "kilix", "tls")
        os.makedirs(d, mode=0o700, exist_ok=True)
        cert = os.path.join(d, "cert.pem")
        key = os.path.join(d, "key.pem")
        if not (os.path.exists(cert) and os.path.exists(key)):
            subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048",
                            "-nodes", "-keyout", key, "-out", cert,
                            "-days", "3650", "-subj", "/CN=kilix"],
                           check=True, capture_output=True)
            os.chmod(key, 0o600)
        fp = subprocess.run(["openssl", "x509", "-in", cert, "-noout",
                             "-fingerprint", "-sha256"],
                            capture_output=True, text=True).stdout.strip()
        return cert, key, fp

    # ---- browser / HLS bridge ----------------------------------------------
    def ensure_novnc(self):
        d = os.path.join(_data_home(), "kilix", "novnc")
        if not os.path.exists(os.path.join(d, "vnc.html")):
            os.makedirs(os.path.dirname(d), exist_ok=True)
            subprocess.run(["git", "clone", "--depth", "1", "-b", "v1.5.0",
                            "https://github.com/novnc/noVNC", d],
                           check=True, capture_output=True)
        return d

    def ensure_hlsjs(self):
        d = os.path.join(_data_home(), "kilix", "hlsjs")
        f = os.path.join(d, "hls.min.js")
        if not os.path.exists(f):
            os.makedirs(d, exist_ok=True)
            subprocess.run(["curl", "-fsSL",
                            "https://cdn.jsdelivr.net/npm/hls.js@1.5.17/dist/hls.min.js",
                            "-o", f], check=True, capture_output=True)
        return d

    def start_bridge(self, *, rfb_port, http_port, token, hlsdir=None,
                     tls=None, lan=False, what="session"):
        """Spawn config/wsbridge.py. Token goes via the env (KILIX_BRIDGE_TOKEN),
        never argv, so it is not visible in `ps`."""
        argv = ["python3",
                os.path.join(os.path.dirname(os.path.abspath(__file__)), "wsbridge.py"),
                "--rfb-port", str(rfb_port), "--http-port", str(http_port),
                "--host", "0.0.0.0" if lan else "127.0.0.1",
                "--novnc", self.ensure_novnc(),
                "--hlsjs", self.ensure_hlsjs(), "--what", what]
        if hlsdir:
            argv += ["--hls", hlsdir]
        if tls:
            argv += ["--tls-cert", tls[0], "--tls-key", tls[1]]
        env = dict(os.environ, KILIX_BRIDGE_TOKEN=token)
        logf = open(os.path.join(self.runtime_dir, "bridge.log"), "wb")
        return self.spawn("bridge", argv, stdout=logf, stderr=logf, env=env)

    # ---- connect instructions ----------------------------------------------
    def print_connect(self, *, what="app", rfb_port=None, http_port=None,
                       full_pw=None, view_pw=None, token=None,
                       lan_host=None, tls_fp=None, hls_path="hls/live.m3u8"):
        user, host = _whoami(), _hostname()
        # flush every line: a serve process then blocks forever, so buffered
        # connect instructions (e.g. when stdout is a pipe) would never appear.
        w = lambda *a: print(*a, flush=True)
        w(f"\n\x1b[1mkilix serve — {what} is streaming\x1b[0m  (Ctrl+C to stop)")
        w("  Loopback-only by default; reach it from another device over SSH:")
        if rfb_port:
            w(f"   native VNC : ssh -N -L {rfb_port}:127.0.0.1:{rfb_port} {user}@{host}")
            w(f"                then open a VNC viewer on  localhost:{rfb_port}")
            if full_pw:
                w(f"                control password  : {full_pw}")
            if view_pw:
                w(f"                view-only password : {view_pw}")
        if http_port:
            q = f"?t={token}" if token else ""
            w(f"   browser    : ssh -N -L {http_port}:127.0.0.1:{http_port} {user}@{host}")
            w(f"                then open  http://localhost:{http_port}/{q}")
            w(f"   view (mpv) : mpv http://localhost:{http_port}/{hls_path}{q}")
        if lan_host and http_port:
            q = f"?t={token}" if token else ""
            w(f"   LAN (TLS)  : https://{lan_host}:{http_port}/{q}")
            if tls_fp:
                w(f"                accept cert  {tls_fp}")
        w("")
