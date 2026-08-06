# Driving Kilix from an agent

How a program running *inside* a Kilix pane can find the other panes, open new
ones, read what is on them, and type into them.

This is not a guide to hacking on Kilix. It is a guide for automated callers —
coding agents, scripts, supervisors — that live in a pane and want to use the
terminal around them as a workspace.

Everything below is reachable from an ordinary shell in a pane. There is no
daemon to start and no socket path to guess.


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


## 2. Two interfaces

**Prefer the `kilix` verbs.** They are stable, they handle credential and
binary resolution for you, and they print human-readable tables:

```
kilix ls              kilix new-pane        kilix watch
kilix ls --panes      kilix new-tab         kilix focus
                      kilix fullscreen
```

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

For programmatic use, ask for the raw state instead of parsing the table:

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

`kilix watch` is the read primitive. `--once` prints a single snapshot and
exits — that is the form automation wants:

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
environment, then send:

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

The trailing newline is the Enter key. Without it the text lands on the command
line and just sits there.

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
| text appears but nothing runs | no trailing newline in the payload |
| `id N is ambiguous` | bare ID matches a tab and a pane; qualify it |
| pane vanishes immediately | its command exited; use `--hold` |
| new pane appears on the wrong side | engine predates `left`/`up`; restart Kilix |
| `No matching windows for expression` | the pane already closed |
