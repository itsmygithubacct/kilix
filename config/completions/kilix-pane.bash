# Completion for the `pane` and `tab` verbs defined in kilix.bashrc section 7.
#
# Ids are read live from `kilix pane list --json` and `kilix tab list --json`,
# so the offered targets cannot drift from what the session actually holds.
# Nothing is cached: a fresh listing per invocation, matching the rule that this
# layer must not become a second engine.
#
# The id reader is deliberately tolerant about shape. It collects integer `id`
# fields found under a `windows`/`panes` container (the nested form `kitten @ ls`
# produces) and falls back to a flat top-level list of objects carrying `id`.
# Completion is a convenience: any shape it cannot read yields no suggestions
# rather than an error.

_kilix_verb_ids() {
    local verb="$1" json
    json="$(command kilix "$verb" list --json 2>/dev/null)" || return 0
    [ -n "$json" ] || return 0
    printf '%s' "$json" | command python3 -c '
import json, sys

containers = {"pane": ("windows", "panes"), "tab": ("tabs",)}[sys.argv[1]]
try:
    document = json.load(sys.stdin)
except Exception:
    raise SystemExit(0)

def is_id(value):
    return isinstance(value, int) and not isinstance(value, bool)

found = []

def walk(node, collect):
    # `collect` is true only for the direct elements of a container list, so a
    # tab listing does not also harvest the window ids nested beneath it.
    if isinstance(node, dict):
        if collect and is_id(node.get("id")):
            found.append(node["id"])
        for key, value in node.items():
            walk(value, key in containers)
    elif isinstance(node, list):
        for value in node:
            walk(value, collect)

walk(document, False)
if not found and isinstance(document, list):
    found = [e["id"] for e in document if isinstance(e, dict) and is_id(e.get("id"))]

seen = set()
for value in found:
    if value not in seen:
        seen.add(value)
        print(value)
' "$verb" 2>/dev/null
}

_kilix_pane_complete() {
    local cur prev sub
    cur="${COMP_WORDS[COMP_CWORD]}"
    prev="${COMP_WORDS[COMP_CWORD-1]}"
    sub="${COMP_WORDS[1]:-}"
    COMPREPLY=()

    if [ "$prev" = "--cwd" ]; then
        mapfile -t COMPREPLY < <(compgen -d -- "$cur")
        compopt -o filenames 2>/dev/null
        return 0
    fi
    if [ "$prev" = "--extent" ]; then
        mapfile -t COMPREPLY < <(compgen -W "screen all" -- "$cur")
        return 0
    fi

    if [ "$COMP_CWORD" -eq 1 ]; then
        mapfile -t COMPREPLY < <(compgen -W \
            "right left up down above below quad list close focus read send" -- "$cur")
        return 0
    fi

    case "$sub" in
        close|focus|read|send)
            if [ "$COMP_CWORD" -eq 2 ]; then
                mapfile -t COMPREPLY < <(compgen -W "$(_kilix_verb_ids pane)" -- "$cur")
                return 0
            fi
            ;;
    esac

    case "$sub" in
        list)  mapfile -t COMPREPLY < <(compgen -W "--json --tree" -- "$cur") ;;
        read)  mapfile -t COMPREPLY < <(compgen -W "--extent" -- "$cur") ;;
        send)  mapfile -t COMPREPLY < <(compgen -W "--submit" -- "$cur") ;;
        close|focus) ;;
        *)     mapfile -t COMPREPLY < <(compgen -W "--cwd --hold --porcelain --" -- "$cur") ;;
    esac
    return 0
}

_kilix_tab_complete() {
    local cur prev sub
    cur="${COMP_WORDS[COMP_CWORD]}"
    prev="${COMP_WORDS[COMP_CWORD-1]}"
    sub="${COMP_WORDS[1]:-}"
    COMPREPLY=()

    if [ "$prev" = "--cwd" ]; then
        mapfile -t COMPREPLY < <(compgen -d -- "$cur")
        compopt -o filenames 2>/dev/null
        return 0
    fi

    if [ "$COMP_CWORD" -eq 1 ]; then
        mapfile -t COMPREPLY < <(compgen -W \
            "new left right move list close rename focus" -- "$cur")
        return 0
    fi

    case "$sub" in
        move)
            if [ "$COMP_CWORD" -eq 2 ]; then
                mapfile -t COMPREPLY < <(compgen -W "left right" -- "$cur")
                return 0
            fi
            ;;
        close|focus)
            if [ "$COMP_CWORD" -eq 2 ]; then
                mapfile -t COMPREPLY < <(compgen -W "$(_kilix_verb_ids tab)" -- "$cur")
                return 0
            fi
            ;;
    esac

    case "$sub" in
        list) mapfile -t COMPREPLY < <(compgen -W "--json --tree" -- "$cur") ;;
        new)  mapfile -t COMPREPLY < <(compgen -W "--cwd --porcelain --" -- "$cur") ;;
        *)    ;;
    esac
    return 0
}

complete -F _kilix_pane_complete pane
complete -F _kilix_tab_complete tab
