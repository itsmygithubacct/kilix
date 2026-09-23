# Driving Kilix from an agent

**0.2.2 source-selection note:** the host's default toolkit installer and
Content package pin `af7e848` provides the interactive switcher. The richer
`kilix panes list/dump/wait/send` and JSON interfaces described in this guide
require a later compatible toolkit; the wrapper verb alone does not establish
that they are installed. Use the [version-specific operation guide](help/operations/README.md)
and the basic `ls`, `focus`, `watch` and authenticated `kitten @` paths with the
default pin. The same-OS-window uncredentialed send-text policy is separate
from the narrower authenticated broker-session byte-input checker below.

How a program associated with a Kilix pane can find the other panes, open new
ones, read what is on them, and type into them. The ordinary path is a process
running directly inside a pane. Section 1 also covers agent tool runners that
are launched from a pane but strip the pane environment from their subprocesses.

This is not a guide to hacking on Kilix. It is a guide for automated callers —
coding agents, scripts, supervisors — that usually live in a pane and want to
use the terminal around them as a workspace.

Everything below is reachable from an ordinary shell in a pane. There is no
daemon to start. In-pane callers receive the socket and credential paths
directly; an out-of-pane tool runner must use the bounded discovery procedure
below rather than guessing.


## 1. Are you inside Kilix?

Check `KITTY_LISTEN_ON`. If it is set, a live remote-control socket exists and
everything in this document works:

```sh
[ -n "$KITTY_LISTEN_ON" ] || { echo "not inside a live Kilix"; exit 1; }
```

Four environment variables matter, all set per pane:

| Variable | Meaning |
|---|---|
| `KITTY_LISTEN_ON` | the remote-control socket (`unix:@kilix-<pid>`) |
| `KITTY_WINDOW_ID` | **your own** pane ID — your identity in every listing below |
| `KITTY_PTY_BROKER_SESSION` | your pane's broker session; the key to typing into panes (§6) |
| `KILIX_RC_PASSWORD_FILE` | path to the credential authorising remote control (§7) |

`KITTY_PTY_BROKER_SESSION` is **per pane**, not per terminal. Two panes in the
same window have different values. That is what makes it usable as a precise
target.

### When an agent runner stripped the pane variables

Some agent runtimes execute tools as children of the pane's agent process but
do not copy `KITTY_LISTEN_ON`, `KITTY_WINDOW_ID`, or
`KILIX_RC_PASSWORD_FILE`. An empty variable in that subprocess is therefore
not proof that the user has no live Kilix. If the user has explicitly asked
you to drive an existing Kilix pane, recover the connection narrowly:

```bash
mapfile -t KILIX_TO_CANDIDATES < <(
  ss -xl 2>/dev/null |
    awk '{
      for (i = 1; i <= NF; i++)
        if ($i ~ /^@kilix-[0-9]+$/) print "unix:" $i
    }' | sort -u
)

((${#KILIX_TO_CANDIDATES[@]} == 1)) || {
  printf 'expected one live Kilix socket, found %s\n' \
    "${#KILIX_TO_CANDIDATES[@]}" >&2
  exit 1
}

KILIX_TO=${KILIX_TO_CANDIDATES[0]}
KILIX_PID=${KILIX_TO##*-}
KILIX_ENGINE=$(readlink -f "/proc/$KILIX_PID/exe")
KITTEN=$(dirname "$KILIX_ENGINE")/kitten
TARGET_SESSION_HOME=
TARGET_RC_PASSWORD_FILE=
while IFS= read -r -d '' item; do
  case "$item" in
    KILIX_SESSION_HOME=*) TARGET_SESSION_HOME=${item#*=} ;;
    KILIX_RC_PASSWORD_FILE=*) TARGET_RC_PASSWORD_FILE=${item#*=} ;;
  esac
done <"/proc/$KILIX_PID/environ"
KILIX_SESSION_HOME=${TARGET_SESSION_HOME:-$HOME/.local/gpu_terminal/kilix/session}
KILIX_RC_PASSWORD_FILE=${TARGET_RC_PASSWORD_FILE:-$KILIX_SESSION_HOME/rc-password}

[ -x "$KITTEN" ] || { echo "matching kitten not found" >&2; exit 1; }
[ -f "$KILIX_RC_PASSWORD_FILE" ] && [ ! -L "$KILIX_RC_PASSWORD_FILE" ] || {
  echo "unsafe Kilix credential path" >&2; exit 1;
}
[ "$(stat -c '%u:%a:%h' "$KILIX_RC_PASSWORD_FILE")" = \
  "$(id -u):600:1" ] || {
  echo "Kilix credential is not private" >&2; exit 1;
}

krc() {
  "$KITTEN" @ --to "$KILIX_TO" \
    --password-file "$KILIX_RC_PASSWORD_FILE" "$@"
}
krc ls >/dev/null || { echo "Kilix connection failed" >&2; exit 1; }
```

This derives `kitten` from the process that owns the socket, so the client and
engine match. It validates the existing credential's ownership, mode, and link
count without reading or printing the credential. If there are zero sockets,
stop. If there is more than one, query each candidate with its matching
`kitten` and let the pane listing or the user identify the intended instance;
never pick the first one arbitrarily.

This recovery does not reconstruct your source pane ID. Use `krc ls` to find
the pane running the agent from its foreground process and working directory,
then set `SOURCE_PANE` explicitly. Do not infer it from whichever pane happens
to be focused.


## 2. Two interfaces

**Prefer the `kilix` verbs.** They are stable, they handle credential and
binary resolution for you, and they print human-readable tables:

```
kilix ls              kilix new-pane        kilix watch
kilix ls --panes      kilix new-tab         kilix focus
kilix panes            kilix panes --json    kilix fullscreen
```

`kilix panes` is the centralized interface for automation. With no arguments
it opens the Pane Center TUI. `list`, `dump`, `wait`, `focus`, and `send` expose
the same pane/session/broker model as a CLI; `--json` emits the versioned
`kilix.panes/v1` snapshot. Prefer it over joining raw Kitty, `/proc`, rollout,
and broker records yourself.

`kilix split` is an alias for `new-pane`; `kilix new-page` is an alias for
`new-tab`.

**Drop to `kitten @` only for what the verbs don't cover** — closing a pane,
reading raw JSON, or the `send-text` route in §6:

```sh
kitten @ --password-file "$KILIX_RC_PASSWORD_FILE" <command> ...
```

`kitten` sits next to the running engine and is normally already on `PATH`. If
it is not, resolve it under the build directory:

```sh
KITTEN="$KILIX_BUILD_DIRECTORY/current/src/kitty/launcher/kitten"
```

Omitting `--password-file` does not fall back to something weaker — it fails,
sometimes silently. See §7.


## 3. Finding panes

`kilix ls` lists tabs (Kilix calls them *pages*); `kilix ls --panes` lists
individual panes. The `ACT` column marks what currently has focus.

```
$ kilix ls --panes
ACT  #  PANE_ID  TAB_ID  OSWIN  TITLE                     PROC     CWD
     1       66      23      1  build the widget          bash     ~/src
     2       92      34      1  user@host: ~              ssh      ~/src
*    3      106      37      1  supervisor                python   ~
     4      111      37      1  logs                      ssh      ~
```

`PANE_ID` is what every other command takes. Your own pane is
`$KITTY_WINDOW_ID` — useful for "open a pane next to me" and for not reading or
killing yourself by accident.

For programmatic use, ask the Pane Center for its joined state instead of
parsing the table:

```sh
kilix panes --json
```

Each pane record includes its page, foreground process tree, cwd, explicit
activity state, current coding-session metadata, broker attachment/journal
state, and a short description of what it is doing. Codex state is
conservative: a live process whose newest turn boundary is `task_complete` is
`idle`; `task_started` is `working`; missing evidence remains `agent`.

Ask for Kitty's raw state only when you need layout fields the joined snapshot
does not expose:

```sh
kitten @ --password-file "$KILIX_RC_PASSWORD_FILE" ls
```

That returns JSON: a list of OS windows, each with `tabs`, each with `windows`
(panes). Per-pane fields worth knowing are `id`, `title`, `cwd`,
`foreground_processes[].cmdline`, `is_focused`, and `env` — the pane's full
environment, which is how §6 resolves a broker session.


## 4. Targeting

Commands that take a target accept a bare ID or an explicit kind:

```
kilix focus 111            # bare — resolved against tabs, then panes
kilix focus pane:111       # explicit pane
kilix focus tab:37         # explicit tab
```

`window:` and `win:` are accepted as synonyms for `pane:`; `page:` and
`session:` for `tab:`.

A bare ID that matches **both** a live tab and a live pane is rejected rather
than guessed:

```
kilix focus: id 37 is ambiguous; use tab:37 or pane:37
```

Scripts should always qualify the kind. Bare IDs are for humans typing quickly.


## 5. Opening panes and pages

```sh
kilix new-pane                              # a shell to the right
kilix new-pane down                         # below
kilix new-pane --cwd /some/dir right
kilix new-pane right -- ./run-tests.sh --verbose
kilix new-tab --title "build" -- make all
```

Direction is one of `right` (default), `left`, `up`, `down`. Everything after
`--` is the command to run; with no command you get a shell.

Two behaviours that surprise people:

**The split anchors to the calling pane, not the focused one.** `kilix
new-pane` passes `--self` internally, so a pane running in the background still
splits *itself*. Without that, automation running unattended would drop panes
next to whatever the user happened to be looking at.

**`left` and `up` need a current engine.** They are fork-only placements. If
the running engine predates them it would silently put the pane on the *wrong
side*, so Kilix refuses instead:

```
kilix new-pane: this terminal is running an engine that predates 'left' panes
and would put the pane on the wrong side. Restart kilix to pick up the current
build, or use 'kilix new-pane right' for now
```

Restart Kilix, or use the opposite direction.

**A pane closes when its command exits.** `kilix new-pane right -- ls` flashes
and vanishes; you will usually not read anything off it. If you need the output
to stay on screen, keep the pane alive. Either hold it open:

```sh
kitten @ --password-file "$KILIX_RC_PASSWORD_FILE" \
  launch --location=vsplit --hold --title "results" -- ./slow-job.sh
```

…or end the command with something that waits (`; exec bash`, `; read -r`).
`--hold` is not exposed on `kilix new-pane`; go through `launch` for it.


## 6. Reading and typing

### Reading a pane

`kilix panes dump` is the convenient line-oriented primitive:

```sh
kilix panes dump 111 --lines 40             # includes scrollback
kilix panes dump 111 --lines 20 --screen    # visible screen only
kilix panes dump 111 --lines 40 --json      # pane metadata plus text
```

`kilix watch` remains the live read primitive. `--once` prints a single
snapshot and exits:

```sh
kilix watch 111 --once                      # visible screen
kilix watch 111 --once --extent all         # including scrollback
kilix watch 111 --once --plain              # strip ANSI styling
kilix watch 111 --interval 2                # live, repainting every 2s
```

`--extent` is `screen` (default) or `all`. Use `--plain` whenever you intend to
parse the result; without it you will be matching against escape sequences.

The underlying call, if you want it directly:

```sh
kitten @ --password-file "$KILIX_RC_PASSWORD_FILE" \
  get-text --match id:111 --extent screen
```

### Typing into a pane

Input is deliberately the narrowest route in the whole interface. `send-text`
is authorised **only** when the target is matched by broker session — not by
ID, not by title, not by "all panes". Resolve the session from the pane's
environment, then send. `kilix panes send` performs that resolution, rejects
ambiguous targets and the caller's own pane, splits UTF-8 at the 1024-byte
policy boundary, and uses carriage return for an explicit `--enter`:

```sh
kilix panes send 111 'text without submitting it'
kilix panes send 111 --enter 'submit this prompt'
printf 'submit this prompt' | kilix panes send 111 --enter
```

The lower-level equivalent is:

```sh
PANE=111
SESS=$(kitten @ --password-file "$KILIX_RC_PASSWORD_FILE" ls |
  python3 -c '
import json,sys
want = sys.argv[1]
for osw in json.load(sys.stdin):
    for tab in osw.get("tabs", []):
        for win in tab.get("windows", []):
            if str(win.get("id")) == want:
                print((win.get("env") or {}).get("KITTY_PTY_BROKER_SESSION", ""))
' "$PANE")

printf 'uptime\n' | kitten @ --password-file "$KILIX_RC_PASSWORD_FILE" \
  send-text --match "env:KITTY_PTY_BROKER_SESSION=$SESS" --stdin
```

For an ordinary shell, the trailing line feed submits the command; without it
the text lands on the command line and just sits there. Do not generalize that
lower-level example to full-screen programs: `send-text` transmits bytes, not
an abstract Enter key. The Pane Center's `--enter` sends carriage return,
which submits the current Codex TUI as well as an ordinary shell. Treat text
placement and submission as separate target-program operations, then read the
pane back to verify the intended effect.

The rules the authoriser enforces, all of which reject silently if broken:

- the match must be exactly `env:KITTY_PTY_BROKER_SESSION=<16–64 hex chars>`
  and nothing else;
- no `--match-tab`, no `--all`, no `--exclude-active`, no session targeting;
- bracketed paste must be unset or `disable`;
- the payload must be **≤1024 bytes**. Longer input has to be chunked, or
  written to a file that the pane then reads.

Because the target is a single broker session and sessions are per pane, one
send reaches exactly one pane.


## 7. Authorisation, and the one dangerous failure mode

Remote control is credential-gated. The password lives in the file named by
`KILIX_RC_PASSWORD_FILE` (mode 0600, owned by you), and it authorises a fixed
list of commands — the credential is scoped at the terminal, not at the caller,
so holding it does not confer everything:

```
launch  ls  focus-window  focus-tab  get-text  close-window  close-tab
set-tab-title
```

…plus `send-text` through the custom checker in §6, which is deliberately
absent from that list so the checker is its only route.

Anything else — `set-window-title`, `resize-window`, `signal-child` — is
refused. A refused command normally says so, loudly:

```
Error: The user rejected this password or it is disallowed by
remote_control_password in kitty.conf
```

> **`send-text` does not.** It is fire-and-forget: the client does not wait for
> a reply, so a rejected `send-text` **exits 0 and prints nothing** while doing
> absolutely nothing. An agent that trusts the exit code will believe it typed
> a command and then wait forever for output that is never coming.

Never treat a successful `send-text` exit as proof it landed. Read the pane
back:

```sh
before=$(kilix watch "$PANE" --once --plain)
printf 'make test\n' | kitten @ --password-file "$KILIX_RC_PASSWORD_FILE" \
  send-text --match "env:KITTY_PTY_BROKER_SESSION=$SESS" --stdin
sleep 1
after=$(kilix watch "$PANE" --once --plain)
[ "$before" != "$after" ] || echo "send-text did not land — check the match form"
```

The same caveat applies when `kilix panes send` reports `accepted`: that means
the terminal client accepted the bounded request, not that the target program
interpreted it. Verify with `kilix panes dump "$PANE" --lines 20 --screen`.


## 8. Closing up

There is no `kilix close`. Use the allowlisted remote-control commands:

```sh
kitten @ --password-file "$KILIX_RC_PASSWORD_FILE" close-window --match id:112
kitten @ --password-file "$KILIX_RC_PASSWORD_FILE" close-tab    --match id:37
```

Closing a pane kills what runs in it. Check `foreground_processes` in the `ls`
JSON before closing something you did not open — and never match your own
`$KITTY_WINDOW_ID`.

Agents that open panes should clean them up. A long session that splits a pane
per task and never closes one ends up with a page nobody can read.


## 9. Recipes

**Wait for a Codex pane, inspect it, then hand it another prompt.** `idle` is
reported only after an explicit completed turn while the live Codex process
still owns the rollout, so this avoids prompt-shaped screen heuristics:

```sh
PANE=111
kilix panes wait "$PANE" --for idle --timeout 600 || exit
kilix panes dump "$PANE" --lines 20 --screen
kilix panes send "$PANE" --enter 'continue with the next item'
sleep 1
kilix panes dump "$PANE" --lines 20 --screen
```

The final read is required because pane input remains fire-and-forget.

**Start a long job in an existing pane on the right.** Resolve the spatial
neighbor from the split layout instead of assuming that the second JSON row is
the right pane. In a normal in-pane shell, define the same small wrapper used
by the recovery procedure above:

```bash
krc() {
  kitten @ --password-file "$KILIX_RC_PASSWORD_FILE" "$@"
}
SOURCE_PANE=${SOURCE_PANE:-${KITTY_WINDOW_ID:-}}
[ -n "$SOURCE_PANE" ] || {
  echo "set SOURCE_PANE to the pane running the agent" >&2; exit 1;
}
```

If the runner needed the recovery procedure in §1, retain that procedure's
`krc` function and set `SOURCE_PANE` from its pane listing. Resolve exactly one
right neighbor and its broker session:

```bash
read -r TARGET_PANE TARGET_SESSION < <(
  krc ls | python3 -c '
import json
import sys

source = str(sys.argv[1])
for os_window in json.load(sys.stdin):
    for tab in os_window.get("tabs", []):
        windows = {str(w.get("id")): w for w in tab.get("windows", [])}
        current = windows.get(source)
        if current is None:
            continue
        groups = {
            str(group.get("id")): [str(p) for p in group.get("windows", [])]
            for group in tab.get("groups", [])
        }
        candidates = []
        for group_id in (current.get("neighbors") or {}).get("right", []):
            candidates.extend(groups.get(str(group_id), []))
        if len(candidates) != 1:
            raise SystemExit(
                f"source pane has {len(candidates)} panes immediately right; refusing to guess"
            )
        pane_id = candidates[0]
        session = (windows[pane_id].get("env") or {}).get(
            "KITTY_PTY_BROKER_SESSION", ""
        )
        if not session:
            raise SystemExit("right pane has no broker session")
        print(pane_id, session)
        raise SystemExit(0)
raise SystemExit("source pane is not live")
' "$SOURCE_PANE"
)

[[ "$TARGET_SESSION" =~ ^[0-9a-f]{16,64}$ ]] || {
  echo "invalid target broker session" >&2; exit 1;
}
```

Before typing, prove the target is an idle shell. Check both the pane text and
`foreground_processes` in `krc ls`; a prompt-looking final line alone is not
enough if an editor, agent, installer, or password prompt owns the foreground:

```bash
krc get-text --match "id:$TARGET_PANE" --extent screen
pane_processes() {
  krc ls | python3 -c '
import json, sys
want = str(sys.argv[1])
for os_window in json.load(sys.stdin):
    for tab in os_window.get("tabs", []):
        for pane in tab.get("windows", []):
            if str(pane.get("id")) == want:
                for proc in pane.get("foreground_processes") or []:
                    print(" ".join(proc.get("cmdline") or []))
                raise SystemExit(0)
raise SystemExit("target pane is no longer live")
' "$1"
}
pane_processes "$TARGET_PANE"
```

Refuse to send unless that output is empty or contains only the expected idle
shell and the screen visibly ends at its prompt. Then construct a short,
shell-quoted command with an explicit working directory and artifact path, and
run it in the foreground so the pane remains the job's monitor:

```bash
JOB_DIR=$HOME/work/image-builder
ARTIFACT=$JOB_DIR/artifacts/development.iso
printf -v RUN_JOB 'cd %q && OUTPUT_ISO=%q ./build-image.sh' \
  "$JOB_DIR" "$ARTIFACT"
((${#RUN_JOB} + 1 <= 1024)) || {
  echo "command exceeds Kilix's send-text limit" >&2; exit 1;
}

printf '%s\n' "$RUN_JOB" | krc send-text \
  --match "env:KITTY_PTY_BROKER_SESSION=$TARGET_SESSION" \
  --stdin --bracketed-paste=disable
```

Do not use `&`, `nohup`, tmux, or a second broker attachment for this case.
The existing visible Kilix frontend already owns the broker's one read-write
attachment, and the pane itself is the durable display for the foreground job.
Finally, remember that `send-text` cannot report rejection: read the pane back
and confirm the expected process appeared before telling the user it started.

```bash
sleep 1
krc get-text --match "id:$TARGET_PANE" --extent screen
pane_processes "$TARGET_PANE"
```

**Run a job in a side pane and collect its output.** Hold the pane open so the
output survives the command exiting, then read it back:

```sh
ID=$(kitten @ --password-file "$KILIX_RC_PASSWORD_FILE" \
     launch --location=vsplit --hold --title "tests" --cwd "$PWD" \
     -- ./run-tests.sh)
# …poll until it settles…
kilix watch "$ID" --once --extent all --plain > /tmp/tests.log
kitten @ --password-file "$KILIX_RC_PASSWORD_FILE" close-window --match "id:$ID"
```

**Drive something interactive.** Put the interactive program in the pane as its
command, rather than starting a shell and typing the invocation:

```sh
kitten @ --password-file "$KILIX_RC_PASSWORD_FILE" \
  launch --location=vsplit --hold --title "session" \
  -- ssh -t some-host 'sudo journalctl -b -1 -n 40'
```

`ssh -t` forces a PTY so a password prompt is actually reachable. Then poll
with `kilix watch --once` until the prompt appears.

**Watch for a prompt before sending.** Never send blind into a pane that may be
sitting at a password prompt — your text becomes part of the password attempt,
and on failure it may be echoed into logs:

```sh
until kilix watch "$PANE" --once --plain | grep -q '\$ $'; do sleep 1; done
```

**Send more than 1024 bytes.** Write the payload to a file and have the pane
read it, rather than chunking a heredoc through `send-text`:

```sh
cat > /tmp/job.sh <<'EOF'
…long script…
EOF
printf 'bash /tmp/job.sh\n' | kitten @ --password-file "$KILIX_RC_PASSWORD_FILE" \
  send-text --match "env:KITTY_PTY_BROKER_SESSION=$SESS" --stdin
```


## 10. Driving agent panes (Codex, Claude Code)

Other panes often run coding agents rather than shells. Everything in §6 still
applies, but an agent's input box is a full-screen program with its own rules,
and a send that "worked" at the terminal can still reach the agent mangled,
merged into another message, or not at all. These rules come from driving many
agent sessions at once; each one exists because breaking it lost a message or
delivered a wrong one.

### Identify the target by what runs in it

Titles go stale: a pane titled after one project may now be running something
else. Pick targets from the `ls` JSON by `foreground_processes[].cmdline` and
`cwd`, then use the broker session from that same record. Compare it with your
own `$KITTY_PTY_BROKER_SESSION` before sending, so you never type into
yourself. Pane IDs and broker sessions change when a pane restarts; read them
fresh each time, never from a cache.

### Wait for a ready input box, and answer menus explicitly

A freshly started agent can open with a menu instead of an input box. Codex
asks whether to trust a new working directory (`1. Yes`, `2. No, quit`).
Claude Code can show a permissions warning. Menus act on single keypresses, so
a brief sent on a timer can choose an option: a path containing the digit `2`
has answered "No, quit" and closed the session. Poll the screen (§6) until a
ready marker is visible (Codex shows `Ask Codex to do anything` above its
model/status line; Claude Code shows its input prompt and shortcut hint).
Answer any menu with the exact key you intend, then check the screen again.

### Put text in, check it, then submit

Send the text and the submit key as two separate operations, and read the pane
back between them. The read is what stops a malformed prompt reaching a live
agent.

- **Check for the end of the message, not the start.** End every message with
  a short, distinctive token (for example `END-OF-BRIEF-7Q`) and look for that
  token. It matches only if the whole message arrived.
- **Strip whitespace before comparing.** The pane wraps text at its own width,
  so a token can be split across two lines. Compare with spaces and newlines
  removed (`tr -d ' \n\r'` on the captured screen), or a message that did
  arrive looks as if it did not. Sending it again then doubles it.
- **Long text is shown as a placeholder.** Agents collapse a large paste into
  a marker such as `[Pasted Content 1018 chars]` or `[Pasted text #3]`, so your
  words are not on screen. A placeholder alone does not prove the whole text
  arrived, because a truncated paste looks the same.
- **One missed match is not proof of failure.** The screen redraws
  constantly. Read it again a moment later before concluding anything.

### Keep messages short; put briefs in files

Budget about 900 bytes per message, below the 1024-byte policy in §6. An
over-size send has been observed to arrive cut off, leaving a fragment in the
agent's input box, rather than being cleanly refused. Anything longer belongs
in a file: write the brief to disk and send one sentence with its path.

### Busy agents, queued messages and clearing the input box

- A working agent shows markers such as `esc to interrupt` or `Working (…)`.
  Typing still works, but think about when the message will be read.
- While a Codex turn is running, its input box offers **Tab to queue** the
  message. Send the text, check it, then send a Tab character (`\t`) instead
  of the submit byte. The message then waits for the current step to finish
  rather than interrupting it, and appears under "Queued follow-up inputs".
- Ctrl-U does not clear the Codex input box. Repeated DEL (`\177`) does; check
  that the idle placeholder is back before sending anything new.
- If someone else's text is already in an agent's input box, it is theirs. Do
  not append to it; ask, or leave the pane alone.

### Completion tokens and stalled sessions

An agent usually echoes its brief, and with it any "done" token the brief
mentions. A watcher that greps for the bare token can therefore fire while the
work has barely started. Look for the token together with its result (a hash,
a file on disk) and a stopped busy marker.

A session that has stopped is often correct: it reached a real gate and
recorded it. Read the pane or its log (§11) before resuming it, and resume
only when you can say what changed. Put that in the message.

### Working alongside other agents

- Do not edit a checkout another pane is working in. Use your own branch in a
  separate `git worktree`, and tell the other session what changed and what it
  should do with it.
- Say in the message who is sending it and why, so the receiving agent does
  not treat an automated message as its user typing.
- Experiment in a scratch tab, never in a live pane. Open one with
  `launch --type=tab --keep-focus`, compare the set of pane IDs before and
  after, and close only the exact ID you created. A `launch` issued from
  outside a pane opens in whatever tab the user has active.


## 11. Transcripts and complete logs

A pane's screen and scrollback are not the only record, and they are not
complete.

**Kilix transcripts.** Kilix records each session's output on disk, named by
broker session, so a closed pane can still be read. See the
[transcripts help page](help/operations/transcripts.md) for `kilix transcript
list`, `show` and `path`. The transcript is what the pane *displayed*, wrapped
at the pane's width: search it for single words, commit hashes or file names,
never for phrases that may span a line break.

**An agent's own session log.** For an agent pane, the full conversation,
including tool calls and output that scrolled away or was never drawn, lives in
the agent's session log, not in the terminal. Find it from the pane's
foreground process:

```sh
# PID from foreground_processes in the ls JSON for that pane
ls -l /proc/$PID/fd | grep -E 'rollout-.*\.jsonl'     # Codex
```

Codex writes `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl`. Claude Code writes
`~/.claude/projects/<working-directory-slug>/<session-id>.jsonl`, where the
slug is the pane's working directory with `/` replaced by `-`. Claude Code does
not hold the file open, so the `/proc` check finds only Codex logs; for Claude
Code, take the newest file in the directory matching the pane's `cwd`. Both are one
JSON object per line; the user and assistant messages are the fastest way to
recover what a session was asked and what it reported.


## 12. Safety notes for automated callers

- **Tests can write the live store.** Pane shells export `KILIX_DATA_HOME` and
  `KILIX_STORAGE_HOME` pointing at the user's real Kilix data. A test suite run
  with the inherited environment can overwrite installed models or settings.
  Run tests with those variables (and `XDG_*`) pointed at a temporary directory.
- **Some verbs install things.** A `kilix` subcommand can lazily install the
  component it needs, even when you only asked for `--help`. Read the verb's
  documentation instead of probing it.
- **Waiting on a process.** `pgrep -f PATTERN` also matches the shell running
  your wait loop, so `until ! pgrep -f job` never ends. Wait on a recorded PID
  (have the job write `$$` to a file) or check that its output is still growing.


## 13. Failure reference

| Symptom | Cause |
|---|---|
| `no live kilix remote-control socket` | not inside Kilix, or `KITTY_LISTEN_ON` unset |
| `Remote control is disabled…` | no `--password-file`, or the wrong credential |
| `The user rejected this password or it is disallowed…` | command is not on the §7 allowlist |
| `send-text` exits 0, nothing happens | match form is not `env:KITTY_PTY_BROKER_SESSION=…`, or payload >1024 bytes |
| text appears but nothing runs | the target did not receive its submit control; shells normally accept line feed, while full-screen programs may require carriage return or another key sequence |
| `KITTY_LISTEN_ON` is empty although Kilix is visible | the agent runner stripped pane variables; use the bounded §1 recovery and require an unambiguous socket |
| broker `attach` reports `busy` for the target pane | its visible frontend already owns the read-write attachment; use broker-session-scoped `send-text` |
| `id N is ambiguous` | bare ID matches a tab and a pane; qualify it |
| pane vanishes immediately | its command exited; use `--hold` |
| new pane appears on the wrong side | engine predates `left`/`up`; restart Kilix |
| `No matching windows for expression` | the pane already closed |
| an agent pane quit right after your brief | it opened with a menu and a character in your text chose an option; wait for the ready marker (§10) |
| your check says the text is missing, but it arrived | the pane wrapped your end token across lines, or the screen was mid-redraw; strip whitespace and read again (§10) |
| an agent received half a message | the send exceeded the size budget and was cut off; put long text in a file (§10) |
| a "done" watcher fired immediately | the agent echoed the brief, including the token; require the token plus its result (§10) |
| a closed pane's output is needed | read its transcript, or the agent's session log (§11) |
