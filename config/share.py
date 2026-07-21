"""kilix share — run the WHOLE kilix on a headless Xvfb and stream it to
multiple devices (Phase 3, the faithful text+graphics+video path).

kitty cannot start on TightVNC's Xvnc (missing Xkb extension), so the desktop
runs on Xvfb; pixels go out as HLS (H.264 — any browser/player, many viewers)
and a remote viewer's keyboard/mouse are injected with XTest (the same mechanism
`kilix run` uses). Inside this nested kilix, browse/run/icat keep their fast
local POSIX shared-memory graphics — the network boundary here is pure pixels.

Loopback by default (reach it over SSH); --lan serves the LAN over TLS + token.
Self-contained: serves the HLS segments, vendored hls.js, a view+control page,
and a token-gated /control WebSocket that drives XTest.
"""
import argparse
import asyncio
import hmac
import http
import json
import mimetypes
import os
import signal
import ssl
import sys
import time
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import warnings
import stream
import xinject
import websockets
from Xlib import display as xdisplay

# websockets compat (see config/wsbridge.py): the legacy serve API works on both
# websockets 10.x and 15.x; the default `websockets.serve` does not.
warnings.filterwarnings("ignore", category=DeprecationWarning)
try:
    from websockets.legacy.server import serve as ws_serve
except Exception:
    from websockets.server import serve as ws_serve

mimetypes.add_type("application/vnd.apple.mpegurl", ".m3u8")
mimetypes.add_type("video/mp2t", ".ts")
mimetypes.add_type("video/mp4", ".mp4")
mimetypes.add_type("video/iso.segment", ".m4s")


def _ct_eq(a, b):
    """Constant-time string compare that never raises on non-ASCII input
    (hmac.compare_digest(str,str) TypeErrors on non-ASCII)."""
    try:
        return hmac.compare_digest(a.encode("utf-8", "ignore"), b.encode())
    except Exception:
        return False

# browser KeyboardEvent.key (multi-char) -> X keysym name
BROWSER_KEYMAP = {
    "ArrowUp": "Up", "ArrowDown": "Down", "ArrowLeft": "Left", "ArrowRight": "Right",
    "Enter": "Return", "Backspace": "BackSpace", "Escape": "Escape", "Tab": "Tab",
    "Delete": "Delete", "Home": "Home", "End": "End", "PageUp": "Prior",
    "PageDown": "Next", "Insert": "Insert", " ": "space",
    "Control": "Control_L", "Shift": "Shift_L", "Alt": "Alt_L", "Meta": "Super_L",
    **{f"F{i}": f"F{i}" for i in range(1, 13)},
}

PAGE = """<!doctype html><meta charset=utf-8><title>kilix share</title>
<meta name=viewport content="width=device-width,initial-scale=1">
<style>html,body{margin:0;background:#000;height:100%;overflow:hidden}
#v{width:100vw;height:100vh;object-fit:contain;display:block;outline:none}
#hint{position:fixed;top:8px;left:8px;font:13px system-ui;color:#8ae234;
background:#000a;padding:4px 8px;border-radius:4px}</style>
<video id=v autoplay muted playsinline tabindex=0></video>
<div id=hint>kilix share — click to control · keys &amp; mouse are forwarded</div>
<script src="/hlsjs/hls.min.js"></script>
<script>
var v=document.getElementById('v'),src='/hls/live.m3u8'+location.search;
if(window.Hls&&Hls.isSupported()){var h=new Hls({liveSyncDurationCount:2,liveMaxLatencyDurationCount:6,maxLiveSyncPlaybackRate:1.5});h.loadSource(src);h.attachMedia(v);}
else{v.src=src;}
var ws=new WebSocket((location.protocol=='https:'?'wss://':'ws://')+location.host+'/control'+location.search);
function send(o){if(ws.readyState==1)ws.send(JSON.stringify(o));}
function norm(e){var r=v.getBoundingClientRect();
 var vw=v.videoWidth||r.width,vh=v.videoHeight||r.height;
 var s=Math.min(r.width/vw,r.height/vh),dw=vw*s,dh=vh*s;
 var ox=(r.width-dw)/2,oy=(r.height-dh)/2;
 return {x:Math.max(0,Math.min(1,(e.clientX-r.left-ox)/dw)),
         y:Math.max(0,Math.min(1,(e.clientY-r.top-oy)/dh))};}
v.addEventListener('mousemove',function(e){var p=norm(e);send({t:'m',x:p.x,y:p.y});});
v.addEventListener('mousedown',function(e){e.preventDefault();v.focus();var p=norm(e);send({t:'m',x:p.x,y:p.y,b:e.button+1,d:1});});
v.addEventListener('mouseup',function(e){e.preventDefault();var p=norm(e);send({t:'m',x:p.x,y:p.y,b:e.button+1,d:0});});
v.addEventListener('contextmenu',function(e){e.preventDefault();});
v.addEventListener('wheel',function(e){e.preventDefault();var p=norm(e);send({t:'m',x:p.x,y:p.y,b:e.deltaY<0?4:5});},{passive:false});
v.addEventListener('keydown',function(e){e.preventDefault();send({t:'k',k:e.key,e:1});});
v.addEventListener('keyup',function(e){e.preventDefault();send({t:'k',k:e.key,e:0});});
v.focus();
</script>"""


class Desktop:
    def __init__(self, a):
        self.a = a
        self.w, self.h = a.width, a.height
        self.token = None
        self.sup = stream.StreamSupervisor(f"share-{os.getpid()}")
        self.inj = None

    def setup(self):
        n = self.sup.pick_display()
        self.n = n
        self.sup.start_xvfb(n, self.w, self.h)
        os.environ["XAUTHORITY"] = self.sup.xauth   # only our children reach :n
        kitty = os.environ.get("KILIX_KITTY") or "kitty"
        env = dict(os.environ, DISPLAY=f":{n}", LIBGL_ALWAYS_SOFTWARE="1")
        env.pop("KILIX_STREAM", None)  # nested kilix uses local t=s graphics
        self.monitor = None
        if self.a.audio:                  # whole-desktop audio -> AAC in the HLS
            sink, self.monitor = self.sup.make_null_sink(f"share-{os.getpid()}")
            if sink:
                env["PULSE_SINK"] = sink
        argv = [kitty, "--class", "kilix",
                "-o", f"initial_window_width={self.w}",
                "-o", f"initial_window_height={self.h}",
                "-o", "remember_window_size=no",
                "-o", "confirm_os_window_close=0"]
        logf = stream._private_open(
            os.path.join(self.sup.runtime_dir, "kitty.log"), "wb")
        self.sup.spawn("kitty", argv, env=env, stdout=logf, stderr=logf)
        xd = xdisplay.Display(f":{n}")
        deadline = time.time() + 15
        while time.time() < deadline:     # wait for a viewable kitty window
            try:
                if any(c.get_attributes().map_state == 3
                       for c in xd.screen().root.query_tree().children):
                    break
            except Exception:
                pass
            time.sleep(0.25)
        self.inj = xinject.Injector(xd, self.w, self.h)
        self.hlsdir = os.path.join(self.sup.runtime_dir, "hls")
        self.sup.start_hls(n, self.w, self.h, self.hlsdir, fps=self.a.fps,
                           debug=self.a.debug, audio=self.monitor)
        self.hlsjs = self.sup.ensure_hlsjs()
        self.token = self.sup.mint_token()
        self.http_port = stream.free_port()
        self.tls = self.sup.tls_cert() if self.a.lan else None

    # ---- auth / serving -----------------------------------------------------
    def authed(self, path, headers):
        if not self.token:
            return True
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(path).query)
        t = (q.get("t") or [None])[0]
        if t and _ct_eq(t, self.token):
            return True
        for part in (headers.get("Cookie", "") or "").split(";"):
            if "=" in part:
                k, v = part.strip().split("=", 1)
                if k == "kilix_t" and _ct_eq(v, self.token):
                    return True
        return False

    def has_qtok(self, path):
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(path).query)
        t = (q.get("t") or [None])[0]
        return bool(t and _ct_eq(t, self.token))

    def resp(self, status, body, ctype="text/plain", cookie=None,
             cache=None):
        if isinstance(body, str):
            body = body.encode()
        h = [("Content-Type", ctype), ("Content-Length", str(len(body)))]
        if cache:
            h.append(("Cache-Control", cache))
        if cookie:
            h.append(("Set-Cookie", f"kilix_t={cookie}; Path=/; SameSite=Strict"))
        return http.HTTPStatus(status), h, body

    def file(self, root, rel, cookie=None):
        rel = urllib.parse.unquote(rel)
        full = os.path.normpath(os.path.join(root, rel))
        if full != root and not full.startswith(root + os.sep):
            return self.resp(403, b"forbidden")
        if not os.path.isfile(full):
            return self.resp(404, b"not found")
        ct = mimetypes.guess_type(full)[0] or "application/octet-stream"
        cache = ("no-cache" if full.endswith(".m3u8") else
                 "max-age=60, immutable"
                 if full.endswith((".ts", ".m4s", ".mp4")) else
                 "max-age=86400" if full.endswith(".js") else None)
        with open(full, "rb") as f:
            return self.resp(200, f.read(), ct, cookie, cache)

    async def process_request(self, path, headers):
        route = urllib.parse.urlsplit(path).path
        cookie = self.token if self.has_qtok(path) else None
        if route == "/control":
            return None if self.authed(path, headers) else self.resp(401, b"unauthorized")
        if route in ("/", "/index.html"):
            if not self.authed(path, headers):
                return self.resp(401, b"unauthorized - append ?t=<token>")
            return self.resp(200, PAGE, "text/html", cookie)
        if route.startswith("/hls/"):
            if not self.authed(path, headers):
                return self.resp(401, b"unauthorized")
            return self.file(self.hlsdir, route[len("/hls/"):], cookie)
        if route.startswith("/hlsjs/"):
            return self.file(self.hlsjs, route[len("/hlsjs/"):])
        return self.resp(404, b"not found")

    async def ws_handler(self, ws, path):
        if not self.authed(path, ws.request_headers):
            await ws.close(1008, "unauthorized")
            return
        try:
            async for msg in ws:
                try:
                    self.inject(json.loads(msg))
                except Exception:
                    continue
        except Exception:
            pass
        finally:
            # a viewer that drops mid-drag/keypress must not leave a button or
            # modifier stuck down on the shared desktop.
            self.inj.release_all()

    def inject(self, ev):
        try:
            if ev.get("t") == "m":
                x = ev.get("x", 0) * self.w
                y = ev.get("y", 0) * self.h
                b = ev.get("b", 0)
                d = ev.get("d")
                self.inj.move_click(x, y, button=b,
                                    press=(bool(d) if d is not None else None))
            elif ev.get("t") == "k":
                k = ev.get("k", "")
                etype = 1 if ev.get("e", 1) == 1 else 3
                if len(k) == 1:
                    self.inj.key(k, etype)
                elif k in BROWSER_KEYMAP:
                    self.inj.key_named(BROWSER_KEYMAP[k], etype)
        except Exception:
            pass

    def announce(self):
        lan_host = stream.lan_ip() if self.a.lan else None
        self.sup.print_connect(what="whole desktop", http_port=self.http_port,
                               token=self.token, lan_host=lan_host,
                               tls_fp=self.tls[2] if self.tls else None,
                               hls_path="hls/live.m3u8")
        print("  \x1b[1;33m!  this shares your ENTIRE kilix desktop "
              "(view + control)\x1b[0m\n", flush=True)


async def _serve(d):
    ssl_ctx = None
    if d.tls:
        ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ssl_ctx.load_cert_chain(d.tls[0], d.tls[1])
    host = "0.0.0.0" if d.a.lan else "127.0.0.1"
    async with ws_serve(d.ws_handler, host, d.http_port,
                        process_request=d.process_request,
                        max_size=None, ping_interval=None, ssl=ssl_ctx):
        await asyncio.Future()


def main():
    ap = argparse.ArgumentParser(prog="kilix share")
    ap.add_argument("--size", default="1280x800")
    ap.add_argument("--fps", type=int, default=15)
    ap.add_argument("--lan", action="store_true")
    ap.add_argument("--hls", action="store_true")   # accepted for symmetry (always on)
    ap.add_argument("--audio", action="store_true")  # desktop audio -> AAC in the HLS
    ap.add_argument("--debug", action="store_true")  # ffmpeg encode metrics (fps/bitrate) -> runtime dir
    a = ap.parse_args()
    if a.debug:
        os.environ["KILIX_DEBUG"] = "1"
    a.width, a.height = (int(v) for v in a.size.lower().split("x"))
    for _s in (signal.SIGTERM, signal.SIGHUP):     # -> atexit -> cleanup
        signal.signal(_s, lambda *_: sys.exit(0))
    d = Desktop(a)
    d.setup()
    d.announce()
    try:
        asyncio.run(_serve(d))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
