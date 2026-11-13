"""agent-message picks a native channel or names the fallback; it never types."""
import http.server
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "config"))
import agent_message as am

FOOTER = "  GPT-6-Astra max · ~/project · Follow repository commit rules"


def screen(footer=FOOTER):
    return "history\n\n› Ask Codex to do anything\n\n" + footer + "\n  ← for agents · ? for shortcuts\n"


def codex_pane(pane_id=6, pid=None, cwd="/elsewhere"):
    return {"id": pane_id, "cwd": cwd, "foreground_processes": [
        {"pid": 1, "cmdline": ["node", "/usr/local/bin/codex", "--yolo"]},
        {"pid": pid or os.getpid(), "cmdline": ["/opt/vendor/bin/codex", "--yolo"]}]}


class FooterTests(unittest.TestCase):
    def test_cwd_and_thread_name(self):
        self.assertEqual(am.footer(screen()), ("~/project", "Follow repository commit rules"))

    def test_name_may_contain_the_separator(self):
        self.assertEqual(am.footer(screen(FOOTER + " · part two"))[1],
                         "Follow repository commit rules · part two")

    def test_new_thread_shows_no_name(self):
        self.assertEqual(am.footer(screen("  gpt-5.5 high · ~/project")), ("~/project", None))

    def test_other_programs_have_no_footer(self):
        self.assertEqual(am.footer("$ ls\nREADME\n"), (None, None))

    def test_truncated_names_match_by_prefix_only(self):
        self.assertTrue(am.name_matches("Follow repo…", "Follow repository commit rules"))
        self.assertFalse(am.name_matches("Follow repo…", "Fix repository"))
        self.assertFalse(am.name_matches("…", "anything"))
        self.assertFalse(am.name_matches("Follow", "Follow repository commit rules"))


class ClassifyTests(unittest.TestCase):
    def setUp(self):
        self.cwd = os.getcwd()
        home = str(Path.home())
        shown = "~" + self.cwd[len(home):] if self.cwd.startswith(home + "/") else self.cwd
        self.screen = screen(f"  GPT-6-Astra max · {shown} · Lane work")

    def thread(self, tid="t-1", name="Lane work", cwd=None):
        return {"id": tid, "name": name, "cwd": cwd or self.cwd, "status": "idle"}

    def test_one_matching_daemon_thread_is_the_native_route(self):
        route = am.classify(codex_pane(), self.screen, [self.thread(), self.thread("t-2", "Other")])
        self.assertEqual((route["route"], route["thread"]), ("codex-native", "t-1"))

    def test_process_cwd_wins_over_the_pane_cwd(self):
        route = am.classify(codex_pane(cwd=self.cwd), self.screen, [self.thread(cwd="/elsewhere")])
        self.assertEqual(route["route"], "screen")

    def test_two_matching_threads_are_refused(self):
        with self.assertRaisesRegex(am.MessageError, "matches 2 loaded Codex threads"):
            am.classify(codex_pane(), self.screen, [self.thread("a"), self.thread("b")])

    def test_in_process_tui_falls_back_to_the_screen(self):
        route = am.classify(codex_pane(), self.screen, [])
        self.assertEqual(route["route"], "screen")
        self.assertIn("in-process", route["reason"])
        self.assertIn("kilix agent-control deliver --pane 6", route["next"])

    def test_no_daemon_or_no_name_falls_back(self):
        self.assertEqual(am.classify(codex_pane(), self.screen, None)["route"], "screen")
        unnamed = screen("  gpt-5.5 high · ~/project")
        self.assertEqual(am.classify(codex_pane(), unnamed, [self.thread(name=None)])["route"],
                         "screen")

    def test_footer_cwd_disagreeing_with_the_process_is_refused(self):
        route = am.classify(codex_pane(), screen("  gpt-5.5 high · /tmp/other · Lane work"),
                            [self.thread()])
        self.assertEqual(route["route"], "screen")

    def test_claude_pane_reports_its_sendmessage_peer(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "4242.json").write_text(json.dumps(
                {"pid": 4242, "sessionId": "s-1", "name": "gpu-terminal-43"}))
            pane = {"id": 9, "foreground_processes": [{"pid": 4242, "cmdline": ["claude"]}]}
            route = am.classify(pane, "", None, sessions=tmp)
            self.assertEqual(route["route"], "claude-sendmessage")
            self.assertEqual(route["peer"], {"name": "gpu-terminal-43", "pid": 4242,
                                             "session_id": "s-1"})
            pane["foreground_processes"][0]["pid"] = 4343
            self.assertEqual(am.classify(pane, "", None, sessions=tmp)["route"], "screen")

    def test_a_shell_has_no_native_channel(self):
        pane = {"id": 3, "foreground_processes": [{"pid": 7, "cmdline": ["bash"]}]}
        self.assertEqual(am.classify(pane, "$ ", None)["route"], "screen")


class CommandLineTests(unittest.TestCase):
    def run_cli(self, *args, home):
        env = {"PATH": "/usr/bin:/bin", "HOME": home, "XDG_STATE_HOME": home + "/state"}
        return subprocess.run([str(ROOT / "kilix"), "agent-message", *args], env=env, cwd=home,
                              text=True, capture_output=True, timeout=30)

    def test_help_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            done = self.run_cli("--help", home=tmp)
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertIn("never types", (ROOT / "config/agent_message.py").read_text())
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_bad_arguments_fail_before_any_connection(self):
        with tempfile.TemporaryDirectory() as tmp:
            done = self.run_cli("send", "--thread", "t", "--text", "hi", "--message-id", "bad id",
                                home=tmp)
            self.assertEqual(done.returncode, 1)
            self.assertIn("--message-id", json.loads(done.stderr)["error"])
            done = self.run_cli("send", "--thread", "t", "--text", " ", home=tmp)
            self.assertIn("empty", json.loads(done.stderr)["error"])
            self.assertEqual(list(Path(tmp).iterdir()), [])


class Model(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()

        def event(kind, **value):
            self.wfile.write(("event: " + kind + "\ndata: " +
                              json.dumps({"type": kind, **value}) + "\n\n").encode())
            self.wfile.flush()
        try:
            event("response.created", response={"id": "r", "object": "response",
                                                "status": "in_progress", "output": []})
            event("response.completed", response={"id": "r", "object": "response",
                  "status": "completed", "output": [],
                  "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}})
        except (BrokenPipeError, ConnectionResetError):
            pass


@unittest.skipUnless(shutil.which("codex"), "a Codex binary is needed for the native round trip")
class NativeRoundTripTests(unittest.TestCase):
    """A private Codex app-server and a loopback model: nobody's real session."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="kam-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        model = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Model)
        threading.Thread(target=model.serve_forever, daemon=True).start()
        self.addCleanup(model.server_close)
        self.addCleanup(model.shutdown)
        (self.root / "codex").mkdir()
        self.socket = self.root / "api.sock"
        env = {"HOME": str(self.root), "CODEX_HOME": str(self.root / "codex"),
               "PATH": os.environ["PATH"]}
        argv = [shutil.which("codex"), "-c", 'model_provider="mock"',
                "-c", 'model_providers.mock.name="mock"',
                "-c", f'model_providers.mock.base_url="http://127.0.0.1:{model.server_port}/v1"',
                "-c", 'model_providers.mock.wire_api="responses"',
                "app-server", "--listen", "unix://" + str(self.socket)]
        process = subprocess.Popen(argv, cwd=self.root, env=env, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        def stop():
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        self.addCleanup(stop)
        deadline = time.monotonic() + 15
        while not self.socket.exists():
            if process.poll() is not None or time.monotonic() > deadline:
                self.fail("private Codex app-server did not start")
            time.sleep(0.05)
        self.ralph = am.load_ralph()
        client = self.ralph.native.CodexSocket(self.socket)
        self.addCleanup(client.close)
        client.call("initialize", {"clientInfo": {"name": "kam-test", "version": "0"},
                                   "capabilities": {"experimentalApi": True}})
        client.send({"method": "initialized"})
        self.client = client
        self.thread = client.call("thread/start", {"model": "test-model", "cwd": os.getcwd(),
                                  "approvalPolicy": "never", "sandbox": "read-only"})["thread"]["id"]
        client.call("thread/name/set", {"threadId": self.thread, "name": "Lane work"})

    def test_threads_lists_the_loaded_thread_without_a_pane(self):
        done = subprocess.run([str(ROOT / "kilix"), "agent-message", "threads", "--socket",
                               str(self.socket)], text=True, capture_output=True, timeout=30,
                              env={"PATH": os.environ["PATH"], "HOME": str(self.root)})
        self.assertEqual(done.returncode, 0, done.stderr)
        rows = json.loads(done.stdout)["threads"]
        self.assertEqual([(r["id"], r["name"]) for r in rows], [(self.thread, "Lane work")])

    def test_pane_resolves_and_message_arrives_in_the_thread(self):
        threads = am.daemon_threads(self.ralph, self.socket)
        home = str(Path.home())
        cwd = os.getcwd()
        shown = "~" + cwd[len(home):] if cwd.startswith(home + "/") else cwd
        route = am.classify(codex_pane(), screen(f"  gpt-test-model · {shown} · Lane work"), threads)
        self.assertEqual((route["route"], route["thread"]), ("codex-native", self.thread))
        state = self.root / "queue"
        result = am.deliver(self.thread, "synthetic peer message", "kam-1", 30,
                            self.socket, state)
        self.assertTrue(result["delivered"], result)
        self.assertEqual(result["receipt"]["thread"], self.thread)
        store = self.ralph.store.Store(state)
        try:
            self.assertFalse(store.worker_running(), "the transient worker must stop")
        finally:
            store.close()
        wire = "kilix-ralph:kam-1"
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            turns = self.client.call("thread/turns/list", {"threadId": self.thread, "limit": 10,
                                     "itemsView": "full", "sortDirection": "desc"})["data"]
            ids = [item.get("clientId") for turn in turns for item in turn.get("items", [])]
            queued = [q.get("clientUserMessageId") for q in self.client.call(
                "thread/queue/list", {"threadId": self.thread})["data"]]
            if wire in ids or wire in queued:
                break
            time.sleep(0.1)
        else:
            self.fail("message never reached the thread")
        again = am.deliver(self.thread, "synthetic peer message", "kam-1", 5, self.socket, state)
        self.assertEqual(again["message_id"], "kam-1")
        turns = self.client.call("thread/turns/list", {"threadId": self.thread, "limit": 10,
                                 "itemsView": "full", "sortDirection": "desc"})["data"]
        ids = [item.get("clientId") for turn in turns for item in turn.get("items", [])]
        self.assertLessEqual(ids.count(wire), 1, "a retry with the same ID must not resend")


if __name__ == "__main__":
    unittest.main()
