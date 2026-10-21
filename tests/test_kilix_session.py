"""Real subprocess/PTY integration with fake providers; never contacts a model."""
import base64
from concurrent.futures import ThreadPoolExecutor
import copy
import json
import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import tempfile
import termios
import time
import tomllib
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "config"))
import agent_control as control
import agent_trust
from kilix_session import Launch, inspect_run, launch, list_runs, plan, stop, wait
from kilix_session.store import SessionError, process_identity, run_files

ENTRY = ROOT / "kilix-session"
FAKE = r'''#!/usr/bin/env python3
import base64, json, os, pathlib, signal, sys, time
args=sys.argv[1:]
provider=pathlib.Path(sys.argv[0]).name
run=pathlib.Path(os.environ['KILIX_AGENT_RUN_DIR'])
headless='exec' in args or '--print' in args or '--prompt-file' in args
mode=os.environ.get('FAKE_MODE','normal')
if headless:
    raw=pathlib.Path(args[args.index('--prompt-file')+1]).read_bytes() if '--prompt-file' in args else sys.stdin.buffer.read()
else:
    raw=(run/'prompt.txt').read_bytes()
evidence=dict(argv=args, prompt=base64.b64encode(raw).decode(), cwd=os.getcwd(),
              tty=[os.isatty(i) for i in (0,1,2)], pid=os.getpid(), group=os.getpgrp(),
              foreground=os.tcgetpgrp(0) if os.isatty(0) else None, started=time.monotonic())
(run/'fake.json').write_text(json.dumps(evidence))
if mode=='stubborn': signal.signal(signal.SIGTERM,signal.SIG_IGN)
if not headless:
    print('FAKE-READY',flush=True)
    evidence['input']=sys.stdin.readline()
    import termios
    changed=termios.tcgetattr(0)
    changed[3] &= ~termios.ECHO
    termios.tcsetattr(0,termios.TCSANOW,changed)
    (run/'fake.json').write_text(json.dumps(evidence))
else:
    if provider=='codex': print(json.dumps(dict(type='thread.started',thread_id='observed-thread')),flush=True)
    if provider=='claude': print(json.dumps(dict(type='system',subtype='init',session_id='observed-claude',model='observed-model')),flush=True)
    if mode in ('slow','stubborn'): time.sleep(30)
    if mode=='descendant':
        child=os.fork()
        if child==0:
            time.sleep(30)
            os._exit(0)
        (run/'descendant.pid').write_text(str(child))
    if mode=='large': sys.stdout.write('x'*(2*1024*1024)+'\n')
    if mode=='event-error': print(json.dumps(dict(type='result',is_error=True,result='failure')))
    if provider=='claude': sys.stdout.write(json.dumps(dict(type='result',result='final answer')))
    if provider=='codex': pathlib.Path(args[args.index('--output-last-message')+1]).write_text('final answer')
    print('diagnostic',file=sys.stderr,flush=True)
sys.exit(7 if mode=='exit-error' else 0)
'''


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.home, self.project, self.bin, self.runs = [self.base / n for n in ("home", "project", "bin", "runs")]
        for path in (self.home, self.project, self.bin):
            path.mkdir(mode=0o700)
        for provider in ("codex", "claude", "grok"):
            exe = self.bin / provider
            exe.write_text(FAKE)
            exe.chmod(0o755)
        self.env = {"HOME": str(self.home), "PATH": str(self.bin) + ":/usr/bin:/bin",
                    "LANG": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1",
                    "KILIX_STORAGE_HOME": str(self.base / "store"),
                    "KILIX_AGENT_RUNS_HOME": str(self.runs),
                    "CODEX_HOME": str(self.home / ".codex"),
                    "CLAUDE_CONFIG_DIR": str(self.home / ".claude")}
        patch = mock.patch.dict(os.environ, self.env, clear=True)
        patch.start()
        self.addCleanup(patch.stop)

    def cli(self, *args, env=None, timeout=12):
        return subprocess.run([sys.executable, "-B", str(ENTRY), *args], env=env or self.env,
                              cwd=self.project, capture_output=True, timeout=timeout)

    def spec(self, provider="codex", **updates):
        return Launch(**dict(dict(provider=provider, cwd=str(self.project), interactive=False,
                                  placement="foreground", prompt="Do the requested work.\n"), **updates))

    def until_running(self, record):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            record = inspect_run(record["run_id"])
            if record["status"] == "running" and (Path(record["run_dir"]) / "fake.json").exists():
                return record
            if record["status"] == "failed":
                self.fail(str(record))
            time.sleep(.03)
        self.fail("fake worker did not start")


class SessionTests(Fixture):
    def test_recent_runs_are_ordered_within_a_second(self):
        first = launch(self.spec(placement="new-tab"), client=FakeTerminal())
        second = launch(self.spec(placement="new-tab"), client=FakeTerminal())
        self.assertEqual([r["run_id"] for r in list_runs(limit=2)], [second["run_id"], first["run_id"]])

    def test_queued_cancellation_prevents_provider_and_trust_setup(self):
        record = launch(self.spec(placement="new-tab", trust_folder=True), client=FakeTerminal())
        self.assertTrue(stop(record["run_id"], timeout=0)["stop_requested"])
        result = self.cli("_worker", record["run_id"])
        self.assertEqual(result.returncode, 143, result.stderr)
        self.assertEqual(inspect_run(record["run_id"])["status"], "interrupted")
        self.assertFalse((Path(record["run_dir"]) / "fake.json").exists())
        self.assertFalse((self.home / ".codex").exists())

    def test_missing_executable_after_preparation_is_recorded(self):
        record = launch(self.spec(placement="new-tab"), client=FakeTerminal())
        (self.bin / "codex").unlink()
        result = self.cli("_worker", record["run_id"])
        self.assertEqual(result.returncode, 1)
        saved = inspect_run(record["run_id"])
        self.assertEqual(saved["status"], "failed")
        self.assertIn("No such file", saved["error"])

    def test_worker_uses_launch_config_paths_when_terminal_environment_differs(self):
        record = launch(self.spec(placement="new-tab", trust_folder=True), client=FakeTerminal())
        changed = {**self.env, "CODEX_HOME": str(self.home / "wrong-codex")}
        result = self.cli("_worker", record["run_id"], env=changed)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.home / ".codex/config.toml").exists())
        self.assertFalse((self.home / "wrong-codex").exists())

    def test_runner_signal_is_logged_and_does_not_reach_unrelated_process(self):
        proc = subprocess.Popen([sys.executable, str(ENTRY), "launch", "codex", "--headless", "--foreground",
                                 "--prompt", "Do this task"], env={**self.env, "FAKE_MODE": "slow"},
                                cwd=self.project, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        other = subprocess.Popen(["sleep", "15"], start_new_session=True)
        try:
            deadline = time.monotonic() + 5
            while not list_runs() and time.monotonic() < deadline:
                time.sleep(.03)
            record = self.until_running(list_runs()[0])
            proc.terminate()
            output, error = proc.communicate(timeout=8)
            self.assertEqual(proc.returncode, 143, error)
            self.assertEqual(json.loads(output)["status"], "interrupted")
            self.assertIsNone(other.poll())
            self.assertIsNone(process_identity(record["provider_process"]["pid"]))
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
            other.terminate()
            other.wait()

    def test_wait_and_stop_validate_timeout_before_mutation(self):
        record = launch(self.spec())
        for value in (float("nan"), float("inf"), -1):
            with self.assertRaises(SessionError):
                wait(record["run_id"], timeout=value)
            with self.assertRaises(SessionError):
                stop(record["run_id"], timeout=value)
        self.assertFalse((Path(record["run_dir"]) / "stop.json").exists())

    def test_exact_prompt_argv_and_separate_observations_for_all_clients(self):
        prompt = "quotes ' \" $HOME $(touch SHOULD_NOT_EXIST) `false`\r\nUnicode: café\n\n"
        for provider in ("codex", "claude", "grok"):
            with self.subTest(provider=provider):
                result = launch(self.spec(provider, prompt=prompt, model="requested-model", effort="max"))
                run = Path(result["run_dir"])
                self.assertEqual(result["status"], "completed", result)
                self.assertEqual(result["exit_code"], 0)
                self.assertEqual((run / "prompt.txt").read_bytes(), prompt.encode())
                fake = json.loads((run / "fake.json").read_text())
                self.assertEqual(base64.b64decode(fake["prompt"]), prompt.encode())
                self.assertEqual(fake["argv"], result["argv"][1:])
                self.assertEqual(fake["cwd"], str(self.project))
                self.assertEqual(fake["group"], fake["pid"])
                self.assertFalse(any("bypass" in a or "skip-permissions" in a or a == "--always-approve" for a in fake["argv"]))
                self.assertEqual(result["requested"]["model"], "requested-model")
                self.assertEqual(result["observed"]["model"], "observed-model" if provider == "claude" else None)
                self.assertEqual(result["observed"]["session_id"], {"codex": "observed-thread", "claude": "observed-claude", "grok": None}[provider])
                self.assertFalse(result["agent_ready_verified"])
                self.assertFalse(result["task_completion_verified"])
                for entry in run.iterdir():
                    self.assertEqual(entry.stat().st_mode & 0o777, 0o700 if entry.is_dir() else 0o600, entry)
        self.assertFalse((self.project / "SHOULD_NOT_EXIST").exists())

    def test_headless_file_input_preserves_crlf_and_explicit_stdin(self):
        prompt = self.base / "brief"
        raw = b"one\r\ntwo\n\n"
        prompt.write_bytes(raw)
        result = self.cli("launch", "claude", "--headless", "--foreground", "--prompt-file", str(prompt))
        self.assertEqual(result.returncode, 0, result.stderr)
        record = json.loads(result.stdout)
        self.assertEqual((Path(record["run_dir"]) / "prompt.txt").read_bytes(), raw)
        stdin = subprocess.run([sys.executable, str(ENTRY), "launch", "codex", "--headless", "--foreground", "--prompt-file", "-"],
                               env=self.env, cwd=self.project, input=raw, capture_output=True, timeout=10)
        self.assertEqual(stdin.returncode, 0, stdin.stderr)
        self.assertEqual((Path(json.loads(stdin.stdout)["run_dir"]) / "prompt.txt").read_bytes(), raw)

    def test_dry_run_and_readonly_commands_create_nothing(self):
        planned = plan(self.spec(interactive=True, placement="new-tab", trust_folder=True, prompt="logout\n"))
        self.assertIn("Please read", planned["argv"][-1])
        self.assertEqual(planned["prompt"]["delivery"], "instruction-file")
        self.assertEqual(list_runs(), [])
        self.assertEqual(self.cli("--help").returncode, 0)
        self.assertEqual(self.cli("providers").returncode, 0)
        self.assertFalse(self.runs.exists())
        self.assertFalse((self.home / ".codex").exists())

    def test_failures_events_and_large_raw_streams_are_retained(self):
        for mode, expected in (("event-error", 1), ("exit-error", 7), ("large", 0)):
            with self.subTest(mode=mode), mock.patch.dict(os.environ, {"FAKE_MODE": mode}):
                record = launch(self.spec("claude"))
                self.assertEqual(record["result_code"], expected, record)
                self.assertEqual(record["status"], "failed" if expected else "completed")
                run = Path(record["run_dir"])
                self.assertIn("diagnostic", (run / "stderr.log").read_text())
                self.assertEqual((run / "final.txt").read_text(), "final answer")
                if mode == "large":
                    self.assertGreater((run / "events.jsonl").stat().st_size, 2 * 1024 * 1024)

    def test_background_wait_timeout_stop_and_duplicate_worker(self):
        with mock.patch.dict(os.environ, {"FAKE_MODE": "slow"}):
            record = self.until_running(launch(self.spec(placement="background")))
        self.addCleanup(lambda: stop(record["run_id"]))
        deadline = time.monotonic() + 3
        while record["observed"]["session_id"] is None and time.monotonic() < deadline:
            time.sleep(.03)
            record = inspect_run(record["run_id"])
        self.assertEqual(record["observed"]["session_id"], "observed-thread")
        self.assertTrue(wait(record["run_id"], timeout=0)["wait_timed_out"])
        duplicate = self.cli("_worker", record["run_id"])
        self.assertNotEqual(duplicate.returncode, 0)
        stopped = stop(record["run_id"])
        self.assertEqual(stopped["status"], "interrupted", stopped)
        self.assertEqual(stopped["result_code"], 143)
        self.assertIsNone(process_identity(record["provider_process"]["pid"]))
        self.assertEqual(stop(record["run_id"])["status"], "interrupted")

    def test_stop_escalates_its_own_stubborn_provider(self):
        with mock.patch.dict(os.environ, {"FAKE_MODE": "stubborn"}):
            record = self.until_running(launch(self.spec(placement="background")))
        result = stop(record["run_id"], timeout=8)
        self.assertEqual(result["exit_code"], -signal.SIGKILL, result)
        self.assertEqual(result["status"], "interrupted")

    def test_provider_descendants_are_cleaned_before_leader_is_reaped(self):
        with mock.patch.dict(os.environ, {"FAKE_MODE": "descendant"}):
            record = launch(self.spec())
        pid = int((Path(record["run_dir"]) / "descendant.pid").read_text())
        deadline = time.monotonic() + 2
        while process_identity(pid) and time.monotonic() < deadline:
            time.sleep(.03)
        self.assertIsNone(process_identity(pid))

    def test_missing_caller_records_failure_without_mutating_a_pane(self):
        with mock.patch.object(control, "Client", side_effect=control.ControlError("no caller")):
            record = launch(self.spec(placement="new-tab"))
        self.assertEqual(record["status"], "failed")
        self.assertIn("no caller", record["error"])
        self.assertFalse((Path(record["run_dir"]) / "worker.claim").exists())

    def test_dead_runner_is_observed_as_orphaned_and_never_signalled(self):
        record = launch(self.spec())
        with run_files(None, record["run_id"]) as files:
            saved = files.json("run.json")
            saved.update(status="running", runner={"pid": os.getpid(), "start_ticks": "wrong", "boot_id": "wrong"})
            files.save("run.json", saved)
        self.assertEqual(inspect_run(record["run_id"])["effective_status"], "orphaned")
        with self.assertRaisesRegex(SessionError, "runner is gone"), mock.patch("os.kill") as kill:
            stop(record["run_id"])
        kill.assert_not_called()

    def test_store_rejects_links_public_roots_and_nonmanaged_ids(self):
        self.runs.symlink_to(self.project, target_is_directory=True)
        with self.assertRaises(OSError):
            launch(self.spec())
        self.runs.unlink()
        self.runs.mkdir(mode=0o755)
        self.runs.chmod(0o755)
        with self.assertRaises(SessionError):
            launch(self.spec())
        with self.assertRaises(SessionError):
            inspect_run("../project")
        self.assertEqual(list(self.project.iterdir()), [])

    def test_logs_and_status_cli(self):
        record = launch(self.spec())
        result = self.cli("logs", record["run_id"], "--stream", "final")
        self.assertEqual(result.stdout, b"final answer", result.stderr)
        self.assertEqual(json.loads(self.cli("show", record["run_id"]).stdout)["status"], "completed")

    def test_batch_validates_every_entry_before_launch_and_staggers(self):
        batch = self.base / "batch.json"
        good = ["codex", "--headless", "--background", "--prompt", "Do the task"]
        batch.write_text(json.dumps([good, ["claude", "--headless", "--background"]]))
        failed = self.cli("batch", str(batch), "--stagger", "0")
        self.assertNotEqual(failed.returncode, 0)
        self.assertFalse(self.runs.exists())
        batch.write_text(json.dumps([good, good]))
        started = time.monotonic()
        result = self.cli("batch", str(batch), "--stagger", "0.2")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertGreaterEqual(time.monotonic() - started, .2)
        records = json.loads(result.stdout)["runs"]
        for record in records:
            self.assertEqual(wait(record["run_id"], timeout=5)["status"], "completed")
        times = [json.loads((Path(r["run_dir"]) / "fake.json").read_text())["started"] for r in records]
        self.assertGreaterEqual(times[1] - times[0], .15)


class TrustTests(Fixture):
    def test_linked_client_trust_file_is_left_unchanged(self):
        target = self.home / "outside.json"
        target.write_text('{}')
        config = self.home / ".claude"
        config.mkdir()
        (config / ".claude.json").symlink_to(target)
        record = launch(self.spec("claude", trust_folder=True))
        self.assertEqual(record["status"], "failed")
        self.assertEqual(target.read_text(), '{}')
        self.assertTrue((config / ".claude.json").is_symlink())
        self.assertFalse((Path(record["run_dir"]) / "fake.json").exists())

    def test_trust_is_applied_before_start_and_never_implies_yolo(self):
        for provider in ("codex", "claude"):
            record = launch(self.spec(provider, trust_folder=True))
            self.assertEqual(record["status"], "completed", record)
            self.assertEqual(record["folder_trust"], "trusted")
            self.assertFalse(any("bypass" in arg or "skip-permissions" in arg for arg in record["argv"]))
        codex = tomllib.loads((self.home / ".codex/config.toml").read_text())
        self.assertEqual(codex["projects"][str(self.project)]["trust_level"], "trusted")
        claude = json.loads((self.home / ".claude/.claude.json").read_text())
        self.assertTrue(claude["projects"][str(self.project)]["hasTrustDialogAccepted"])

    def test_malformed_and_explicit_untrusted_config_fail_before_provider(self):
        config = self.home / ".codex/config.toml"
        config.parent.mkdir()
        for value in ("not = [toml", '[projects."' + str(self.project) + '"]\ntrust_level="untrusted"\n'):
            config.write_text(value)
            record = launch(self.spec(trust_folder=True))
            self.assertEqual(record["status"], "failed")
            self.assertEqual(config.read_text(), value)
            self.assertFalse((Path(record["run_dir"]) / "fake.json").exists())

    def test_simultaneous_codex_grants_do_not_lose_projects(self):
        directories = [self.project / str(i) for i in range(12)]
        for path in directories:
            path.mkdir()
        with ThreadPoolExecutor(12) as pool:
            list(pool.map(lambda path: agent_trust.trust("codex", str(path)), directories))
        config = tomllib.loads((self.home / ".codex/config.toml").read_text())
        self.assertEqual(set(config["projects"]), set(map(str, directories)))

    def test_grok_exact_scope_and_trust_flag(self):
        subprocess.run(["git", "init", "-q", str(self.project)], env=self.env, check=True)
        outside = plan(self.spec("grok", trust_folder=True))
        self.assertIn("--trust", outside["argv"])
        child = self.project / "sub"
        child.mkdir()
        with self.assertRaisesRegex(control.ControlError, "repository root"):
            plan(self.spec("grok", cwd=str(child), trust_folder=True))

    def test_permission_bypass_requires_both_inputs(self):
        with mock.patch("kilix_sdk.settings.coding_yolo", return_value=False):
            self.assertNotIn("--dangerously-bypass-approvals-and-sandbox", plan(self.spec(coding_yolo=True))["argv"])
        with mock.patch("kilix_sdk.settings.coding_yolo", return_value=True):
            self.assertNotIn("--dangerously-bypass-approvals-and-sandbox", plan(self.spec())["argv"])
            self.assertIn("--dangerously-bypass-approvals-and-sandbox", plan(self.spec(coding_yolo=True))["argv"])


class InteractiveTests(Fixture):
    def test_native_terminal_foreground_group_and_exact_instruction_file(self):
        for provider in ("codex", "claude", "grok"):
            with self.subTest(provider=provider):
                pid, master = os.forkpty()
                if pid == 0:
                    os.chdir(self.project)
                    os.execve(sys.executable, [sys.executable, "-B", str(ENTRY), "launch", provider,
                        "--foreground", "--prompt", "logout\nExact second line\n"], self.env)
                output, finished = b"", False
                initial_terminal = termios.tcgetattr(master)
                try:
                    deadline = time.monotonic() + 8
                    while b"FAKE-READY" not in output and time.monotonic() < deadline:
                        if select.select([master], [], [], .1)[0]:
                            output += os.read(master, 65536)
                    self.assertIn(b"FAKE-READY", output)
                    os.write(master, b"keyboard input\n")
                    while time.monotonic() < deadline:
                        if select.select([master], [], [], .1)[0]:
                            try:
                                output += os.read(master, 65536)
                            except OSError:
                                pass
                        done, status = os.waitpid(pid, os.WNOHANG)
                        if done:
                            finished = True
                            self.assertEqual(os.waitstatus_to_exitcode(status), 0, output)
                            break
                    self.assertTrue(finished, output)
                    self.assertEqual(termios.tcgetattr(master)[3], initial_terminal[3])
                finally:
                    os.close(master)
                    if not finished:
                        os.kill(pid, signal.SIGKILL)
                        os.waitpid(pid, 0)
                record = next(r for r in list_runs() if r["provider"] == provider)
                fake = json.loads((Path(record["run_dir"]) / "fake.json").read_text())
                self.assertEqual(fake["tty"], [True, True, True])
                self.assertEqual(fake["foreground"], fake["group"])
                self.assertEqual(fake["input"], "keyboard input\n")
                self.assertEqual(base64.b64decode(fake["prompt"]), b"logout\nExact second line\n")
                self.assertIn("Please read", fake["argv"][-1])
                self.assertIsNone(record["observed"]["session_id"])


def pane(number, *, tab=11, layout="splits", group=None):
    return dict(id=number, tab_id=tab, tab_title="existing", os_window_id=1, title="pane", cwd="/tmp",
                layout=layout, env={"KITTY_PTY_BROKER_SESSION": f"{number:032x}"},
                user_vars={"kilix_session_group": group} if group else {}, foreground_processes=[])


class FakeTerminal(control.Client):
    def __init__(self):
        self.caller = 1
        self.command = ["fake", "@", "--to", "fake:unit"]
        self.panes = [pane(1), pane(2, tab=12, layout="grid")]
        self.focus = 1
        self.calls = []
        self.fail_after_launch = False

    def snapshot(self):
        return copy.deepcopy(self.panes)

    def run(self, args, payload=None):
        self.calls.append(args)
        if args == ["launch", "--help"]:
            return "--keep-focus --source-window --next-to --var"
        if args[0] == "launch":
            broker = args[args.index("--match") + 1].split("=", 1)[1]
            anchor = next(p for p in self.panes if p["env"]["KITTY_PTY_BROKER_SESSION"] == broker)
            tab = max(p["tab_id"] for p in self.panes) + 1 if args[args.index("--type") + 1] == "tab" else anchor["tab_id"]
            new = pane(max(p["id"] for p in self.panes) + 1, tab=tab, layout=anchor["layout"])
            for i, arg in enumerate(args):
                if arg == "--var":
                    key, value = args[i+1].split("=", 1)
                    new["user_vars"][key] = value
            self.panes.append(new)
            if "--keep-focus" not in args:
                self.focus = new["id"]
            if self.fail_after_launch:
                raise control.ControlError("lost reply after creating pane")
            return str(new["id"])
        if args[0] == "goto-layout":
            selected = int(args[args.index("--match") + 1].split(":")[1])
            tab = next(p["tab_id"] for p in self.panes if p["id"] == selected)
            for p in self.panes:
                if p["tab_id"] == tab:
                    p["layout"] = args[-1]
        return ""


class PaneTests(Fixture):
    def test_closed_panes_free_capacity_and_manual_panes_count(self):
        terminal = FakeTerminal()
        first = launch(self.spec(placement="grid"), client=terminal)
        tab = first["launch"]["pane"]["tab_id"]
        for i in range(4, 11):
            terminal.panes.append(pane(i, tab=tab, layout="grid"))
        second = launch(self.spec(placement="grid"), client=terminal)
        self.assertNotEqual(second["launch"]["pane"]["tab_id"], tab)
        terminal.panes = [p for p in terminal.panes if p["id"] != 4 and p["tab_id"] != second["launch"]["pane"]["tab_id"]]
        third = launch(self.spec(placement="grid"), client=terminal)
        self.assertEqual(third["launch"]["pane"]["tab_id"], tab)

    def test_new_tab_preserves_focus_and_uses_literal_worker_argv(self):
        terminal = FakeTerminal()
        result = launch(self.spec(placement="new-tab"), client=terminal)
        self.assertEqual(result["launch"]["status"], "submitted", result)
        self.assertEqual(terminal.focus, 1)
        command = next(a for a in terminal.calls if a[0] == "launch" and "--help" not in a)
        self.assertEqual(command[command.index("--") + 1:], result["worker_argv"])
        self.assertIn("--keep-focus", command)

    def test_nine_concurrent_grid_submissions_allocate_eight_plus_one(self):
        terminal = FakeTerminal()
        def submit(_):
            return launch(self.spec(placement="grid", grid="Reviews"), client=terminal)
        with ThreadPoolExecutor(9) as pool:
            records = list(pool.map(submit, range(9)))
        self.assertTrue(all(r["launch"]["status"] == "submitted" for r in records), records)
        tabs = {}
        for p in terminal.panes[2:]:
            tabs[p["tab_id"]] = tabs.get(p["tab_id"], 0) + 1
        self.assertEqual(sorted(tabs.values()), [1, 8])
        self.assertEqual(terminal.focus, 1)
        self.assertEqual(terminal.panes[:2], [pane(1), pane(2, tab=12, layout="grid")])
        self.assertTrue(all("--keep-focus" in a for a in terminal.calls if a[0] == "launch" and "--help" not in a))

    def test_groups_and_changed_layouts_are_not_reused(self):
        terminal = FakeTerminal()
        first = launch(self.spec(placement="grid", grid="first"), client=terminal)
        second = launch(self.spec(placement="grid", grid="second"), client=terminal)
        self.assertNotEqual(first["launch"]["pane"]["tab_id"], second["launch"]["pane"]["tab_id"])
        terminal.panes[2]["layout"] = "splits"
        third = launch(self.spec(placement="grid", grid="first"), client=terminal)
        self.assertNotEqual(first["launch"]["pane"]["tab_id"], third["launch"]["pane"]["tab_id"])

    def test_uncertain_creation_is_retained_without_retry_or_cleanup(self):
        terminal = FakeTerminal()
        terminal.fail_after_launch = True
        record = launch(self.spec(placement="new-tab"), client=terminal)
        self.assertEqual(record["launch"]["status"], "uncertain")
        self.assertEqual(record["effective_status"], "uncertain")
        self.assertEqual(len(terminal.panes), 3)
        self.assertEqual(sum(a[0] == "launch" and "--help" not in a for a in terminal.calls), 1)
        self.assertFalse(any(a[0] in ("close-window", "focus-window") for a in terminal.calls))

    def test_stale_broker_prevents_launch(self):
        terminal = FakeTerminal()
        result = launch(self.spec(placement="new-tab", expect_broker="f" * 32), client=terminal)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(len(terminal.panes), 2)


class InstallTests(Fixture):
    def test_installed_entry_works_outside_source_and_preserves_unrelated_files(self):
        prefix = self.base / "prefix with 'quote"
        script = ROOT / "scripts/install-kilix-session.py"
        for _ in range(2):
            result = subprocess.run([sys.executable, str(script), "--prefix", str(prefix)], env=self.env,
                                    capture_output=True, timeout=8)
            self.assertEqual(result.returncode, 0, result.stderr)
        result = subprocess.run([str(prefix / "bin/kilix-session"), "list"], cwd=self.project,
                                env=self.env, capture_output=True, timeout=8)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["runs"], [])
        (prefix / "bin/kilix-session").write_text("unrelated")
        result = subprocess.run([sys.executable, str(script), "--prefix", str(prefix)], env=self.env,
                                capture_output=True, timeout=8)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((prefix / "bin/kilix-session").read_text(), "unrelated")

    def test_host_dispatch_precedes_engine_setup_and_module_is_importable(self):
        result = subprocess.run(["bash", str(ROOT / "kilix"), "session", "list"],
                                env=self.env, capture_output=True, timeout=8)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.runs.exists())
        self.assertFalse((self.base / "store").exists())
        result = subprocess.run([sys.executable, "-B", "-m", "kilix_session", "providers"],
                                env={**self.env, "PYTHONPATH": str(ROOT / "config")}, capture_output=True, timeout=8)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
