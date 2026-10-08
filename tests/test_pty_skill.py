"""The `kilix-pty` agent skill: its size, its rules, and that every command it teaches runs.

The skill is read by a model that will type what it finds, so a line that does not
parse is a defect. Each `kilix pty ...` line in SKILL.md and its references is run
through the real launcher against a fake broker, with placeholders filled in, and
must not be a usage error. Installation is exercised only in a scratch HOME.
"""
import json
import os
from pathlib import Path
import re
import shlex
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "config"))
from test_pty_cli import SESSION, PtyCliCase, gone_hook, status_json  # noqa: E402
from test_pty_a2r7_docs import normalized  # noqa: E402
import agent_skills as skills  # noqa: E402
import kilix_pty_request as request_module  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "kilix-pty"
SKILL_FILES = [SKILL / "SKILL.md", *sorted((SKILL / "references").glob("*.md"))]
DOC_FILES = [ROOT / "docs" / "AGENTS.md", ROOT / "docs" / "help" / "operations" / "persistence.md",
             ROOT / "README.md"]
if os.environ.get("KILIX_PTY_CONTRACT"):
    DOC_FILES.append(Path(os.environ["KILIX_PTY_CONTRACT"]))
VERBS = {"list", "status", "pane", "observe", "journals", "kill", "capabilities", "request", "reaped",
         "help", "path", "attach", "reap"}
OTHER = "bbbbbbbbbbbbbbbb"
FILL = {"ID": OTHER, "MILLIS": "1700000000000", "PANE_ID": "12", "N": "50",
        "SECONDS": "2", "EPOCH": "1", "OFFSET": "0"}


def frontmatter(text):
    header = text.split("---", 2)[1]
    return dict(re.findall(r"^(\w[\w-]*):\s*(.+)$", header, re.M)), header


def command_lines(text):
    """Every `kilix pty ...` invocation: inline code spans and fenced lines, here-docs apart."""
    found = []
    for span in re.findall(r"`([^`\n]+)`", text):
        found.append(span)
    in_fence = False
    for line in text.splitlines():
        if line.startswith("```"):
            in_fence = not in_fence
        elif in_fence:
            found.append(line)
    commands = []
    for line in found:
        if not line.startswith("kilix pty "):
            continue
        words = line.split()
        if words[2].endswith(":") or words[2] == "...":
            continue
        if words == ["kilix", "pty", "request"] or words == ["kilix", "pty", "journals", "show"]:
            continue  # Interface names in prose, not complete invocations.
        if len(words) > 3 and words[3].endswith(":"):
            continue  # The contract's printed receipt prefix, not a command.
        commands.append(line)
    return commands


def command_forms(line):
    """Materialize every optional flag/alternative in the documentation's usage notation."""
    line = line.split(" #", 1)[0].replace("[bounds]", "[--lines N | --bytes N]")
    for choice, alternatives in (("ID|--pane PANE_ID", ("ID", "--pane PANE_ID")),
                                 ("-|FILE", ("-", "FILE"))):
        if choice in line:
            return [form for alternative in alternatives
                    for form in command_forms(line.replace(choice, alternative))]
    group = re.search(r"\[([^\[\]]+)\]", line)
    if group:
        return [form for alternative in ["", *group.group(1).split("|")]
                for form in command_forms(line[:group.start()] + alternative.strip() + line[group.end():])]
    return [line.replace("...", "list")]


class SkillFileTests(unittest.TestCase):
    def test_skill_md_stays_under_three_kilobytes_and_references_stay_small(self):
        size = (SKILL / "SKILL.md").stat().st_size
        self.assertLessEqual(size, 3000, f"SKILL.md is {size} bytes")
        for reference in (SKILL / "references").glob("*.md"):
            self.assertLessEqual(reference.stat().st_size, 4000, reference.name)

    def test_frontmatter_matches_the_registered_name_and_version(self):
        fields, header = frontmatter((SKILL / "SKILL.md").read_text())
        self.assertEqual(fields["name"], "kilix-pty")
        self.assertIn("kilix-pty", skills.NAMES)
        self.assertIn(f'kilix-version: "{(ROOT / "VERSION").read_text().strip()}"', header)
        record, files, _, descriptions = skills.bundle(ROOT)
        self.assertIn("kilix-pty/SKILL.md", files)
        description = descriptions["kilix-pty"].lower()
        for trigger in ("persistent", "detached", "stuck", "unreachable", "read-only", "journals", "by id"):
            self.assertIn(trigger, description)

    def test_the_rules_are_stated(self):
        text = (SKILL / "SKILL.md").read_text()
        for sentence in (
            "Identity is the full ID from `list`, `pane` or `status`",
            "Never a prefix, title,\n  command or pane id for `kill`",
            "End a session only when the user's own message asks you to end that specific session",
            "`started_millis` from the read you just did: `status`, then `kill`, nothing else",
            "Never end your own session (`$KITTY_PTY_BROKER_SESSION`)",
            "Never pass `--no-caller-check`: stop and report.",
            "`unreachable` is not absent",
            "re-list before any retry, never\n  resend blindly",
            "untrusted pane text",
            "`attach` and `reap` are for people at a terminal, not agents",
            "No preflight",
            "Agents never use the raw `kitty-pty-broker` CLI",
            "`kilix-needle pty` is the cheaper alternative",
            "use only its exact accepted forms",
        ):
            self.assertIn(normalized(sentence), normalized(text))

    def test_the_skill_teaches_the_plain_json_route_and_points_to_capabilities(self):
        text = (SKILL / "SKILL.md").read_text()
        self.assertIn("kilix pty capabilities --json", text)
        self.assertNotIn("request --request-json", text, "the request route belongs in the reference")
        self.assertIn("request --request-json", (SKILL / "references" / "receipts.md").read_text())
        self.assertIn("for structured clients; agents do not need it", normalized(text))

    def test_kilix_skill_points_to_it_and_the_readme_lists_every_skill(self):
        self.assertIn("`kilix-pty`", (ROOT / "skills" / "kilix" / "SKILL.md").read_text())
        readme = (ROOT / "README.md").read_text()
        start = readme.index("### Optional coding-agent skills")
        # The sentence that lists the bundle, not a later mention of one skill.
        listing = readme[start:readme.index("Installation is explicit", start)].split("They cover")[0]
        for name in skills.NAMES:
            self.assertIn(f"`{name}`", listing)

    def test_the_bundle_error_names_the_set_instead_of_counting_it(self):
        source = Path(skills.__file__).read_text()
        self.assertNotIn("exactly the three", source)


class SkillCommandTests(PtyCliCase):
    def setUp(self):
        super().setUp()
        self.reply("list", out="[%s]" % status_json(OTHER))
        self.reply("status", out=status_json(OTHER) + "\n")
        self.reply("observe", out="a\nb\n", err="kitty-pty-broker: cursor=1:4\n")
        self.reply("reaped", out="[]")
        (self.fake / "kill.hook").write_text(gone_hook(self.fake))

    def fill(self, line):
        words = []
        for word in shlex.split(line):
            for placeholder, value in {**FILL, "DIR": str(self.runtime),
                                       "FILE": str(self.tmp / "request.json")}.items():
                word = re.sub(rf"\b{placeholder}\b", value, word)
            words.append(word)
        return words[2:]

    def test_every_command_line_in_the_skill_is_a_valid_invocation(self):
        seen = set()
        for path in SKILL_FILES:
            for line in command_lines(path.read_text()):
                argv = self.fill(line)
                if argv[:2] == ["request", "--request-json"]:
                    continue  # run below with its payload
                result = self.pty(*argv, KITTY_PTY_BROKER_SESSION=SESSION)
                self.assertNotEqual(result.returncode, 2, f"{path.name}: {line}\n{result.stderr}")
                self.assertNotIn("usage:", result.stderr, f"{path.name}: {line}")
                seen.add(argv[0] if argv[0] != "--timeout" else argv[2])
        # Not vacuous: the skill teaches at least these verbs.
        self.assertTrue({"list", "pane", "status", "observe", "journals", "kill", "capabilities"} <= seen, seen)

    def test_every_command_form_in_the_docs_is_a_valid_invocation(self):
        payload = '{"schema":"kilix.pty.request/v1","verb":"list","args":{}}'
        (self.tmp / "request.json").write_text(payload)
        seen = set()
        for path in DOC_FILES:
            for line in command_lines(path.read_text()):
                for form in command_forms(line):
                    argv = self.fill(form)
                    with self.subTest(path=path.name, command=form):
                        if not argv:
                            code, _, stderr = self.pty_tty(KITTY_PTY_BROKER_SESSION=SESSION)
                        elif argv[0] == "request":
                            # Here-doc notation is shell redirection, not part of the CLI argv.
                            if "<<EOF" in argv:
                                argv = argv[:argv.index("<<EOF")]
                            result = self.req(payload, argv=argv)
                            code, stderr = result.returncode, result.stderr
                        elif argv[0] == "attach" or (argv[0] == "observe" and "--once" not in argv):
                            code, _, stderr = self.pty_tty(*argv, KITTY_PTY_BROKER_SESSION=SESSION)
                        else:
                            result = self.pty(*argv, KITTY_PTY_BROKER_SESSION=SESSION)
                            code, stderr = result.returncode, result.stderr
                        self.assertNotEqual(code, 2, stderr)
                        self.assertNotIn("usage:", stderr)
                        seen.add((argv[0] if argv[0] != "--timeout" else argv[2]) if argv else "tui")
        self.assertTrue(VERBS <= seen, seen)

    def test_the_request_example_is_a_valid_request_and_runs(self):
        examples = 0
        for path in [SKILL / "references" / "receipts.md", *DOC_FILES]:
            blocks = re.findall(r"kilix pty request [^\n]*<<'EOF'\n(.+?)\nEOF", path.read_text(), re.S)
            for payload in blocks:
                with self.subTest(path=path.name):
                    request_module.validate(payload.encode())
                    result = self.req(payload)
                    self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
                    self.assertEqual(json.loads(result.stdout)["schema"], "kilix.pty/v1")
                    examples += 1
        self.assertGreaterEqual(examples, 2)  # The skill reference and the README.

    def req(self, payload, argv=None):
        import subprocess
        return subprocess.run(
            ["bash", str(ROOT / "kilix"), "pty", *(argv or ["request", "--request-json", "-"])],
            env=self.env(KITTY_PTY_BROKER_SESSION=SESSION), input=payload, capture_output=True,
            text=True, timeout=60)

    def test_the_kill_line_in_the_skill_sends_one_kill_and_verifies(self):
        result = self.pty("kill", OTHER, "--yes", "--expect-started", "1700000000000", "--json",
                          KITTY_PTY_BROKER_SESSION=SESSION)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["result"], "verified_absent")
        self.assertEqual(len([c for c in self.calls() if " kill " in c]), 1)


class SkillInstallTests(unittest.TestCase):
    def test_the_whole_real_set_installs_and_removes_in_a_scratch_home(self):
        with tempfile.TemporaryDirectory() as scratch:
            home = Path(scratch) / "home"
            home.mkdir(mode=0o700)
            installed = skills.operate("install", "codex", source=ROOT, home=home, env={})
            self.assertEqual(installed["state"], "installed")
            root = home / ".agents/skills"
            self.assertEqual(sorted(p.name for p in root.iterdir()), sorted(skills.NAMES))
            self.assertEqual((root / "kilix-pty" / "SKILL.md").read_bytes(),
                             (SKILL / "SKILL.md").read_bytes())
            self.assertTrue((root / "kilix-pty" / "references" / "receipts.md").is_file())
            removed = skills.operate("remove", "codex", source=ROOT, home=home, env={})
            self.assertEqual(removed["state"], "not-installed")
            self.assertFalse(any(os.path.lexists(root / name) for name in skills.NAMES))


if __name__ == "__main__":
    unittest.main()
