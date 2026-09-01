# Driving Kilix from an agent

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


## 2. Three interfaces

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

**If you are running Python, import the library instead of shelling out.**
`kilix_sdk.panes` is the same model the verbs are built on, and it hands back
ids rather than tables you have to parse:

```python
from kilix_sdk import panes

pid = panes.split("right", cwd="/srv/work")      # returns the new pane id
panes.send(pid, "make test", submit=True)
panes.close(pid)
```

Most agents in this codebase are already Python. Parsing the output of a verb
you could have called as a function is how the five separate re-implementations
of `kitten @ ls` walking got written.

**Drop to `kitten @` only for what neither covers** — it is the escape hatch,
not the interface:

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
kilix new-pane right --cwd /some/dir
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

**A pane closes when its command exits.** `pane right -- ls` flashes and
vanishes; you will usually not read anything off it. Pass `--hold` to keep it
on screen:

```sh
pane right --hold -- ./slow-job.sh
```

…or end the command with something that waits (`; exec bash`, `; read -r`).

**`--porcelain` prints the new id and nothing else**, which is what makes the
verbs composable from a shell:

```sh
pane send "$(pane right --porcelain)" 'make test' --submit
```

**`pane quad` gives you four panes where this one is** — the layout for
supervising several workers at once. In the library it is `panes.quad()`, and
`split(anchor=<id>)` places a pane next to a *named* pane rather than the
focused one; `anchor=` maps onto the engine's existing `--next-to`, so there is
nothing new to install.

**`quad` on a small terminal is refused, not attempted.** Four panes out of an
80×24 terminal are unusable, so `quad` checks the resulting size first and fails
with the actual measurement rather than leaving you a mess to unpick.

One constraint worth knowing: when anchoring over remote control, the engine
ignores the anchor unless the matched pane is in the target tab. Within one tab
— which is every `quad` — that never bites.


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

Agents that open panes should clean them up, and now have the id to do it
with. Every creating call hands one back — `--porcelain` from a verb,
the return value from `panes.split()` and `panes.quad()` — so keep them and
close exactly what you opened:

```python
opened = panes.quad(commands=[["./worker.sh", "a"], ["./worker.sh", "b"], ["./worker.sh", "c"]])
try:
    ...
finally:
    for pid in opened:
        panes.close(pid)
```

Close by remembered id, never by matching a pattern: a pattern cannot tell your
panes from the user's. A long session that splits a pane per task and never
closes one ends up with a page nobody can read.


## 9. Recipes

**Fan four workers out and give each one a task.** This is the case the pane
library exists for: one call makes the layout, and the ids come back in the
order the panes were created, so nothing has to be matched or guessed.

```python
from kilix_sdk import panes

ids = panes.quad(commands=[["./worker.sh", "a"], ["./worker.sh", "b"], ["./worker.sh", "c"]])
for pid, task in zip(ids, tasks):
    panes.send(pid, task, submit=True)
```

`quad` is transactional: if the third split fails, the panes it already made are
closed before it raises, because the caller cannot tell which panes were theirs.

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


## 10. Failure reference

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
