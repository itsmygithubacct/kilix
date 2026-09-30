"""Small standalone CLI for the shared tmux control backend."""

import argparse
import json
import sys

from .control import ControlError, EXITS, SCHEMA, dispatch


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ControlError("EUSAGE", message)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    common = Parser(add_help=False)
    common.add_argument("--socket", default=argparse.SUPPRESS, help="required explicit absolute tmux socket")
    common.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="emit versioned JSON")
    common.add_argument("--dry-run", action="store_true", default=argparse.SUPPRESS, help="validate and resolve without mutation")
    parser = Parser(prog="kilix-tmux", description="Exact tmux control on an explicit socket", parents=[common])
    subs = parser.add_subparsers(dest="operation", required=True)
    subs.add_parser("list", parents=[common], help="list sessions and stable pane IDs")
    new = subs.add_parser("new", parents=[common], help="create a detached default-shell session")
    new.add_argument("name")
    new.add_argument("--cwd", default=argparse.SUPPRESS)
    for verb in ("read", "send", "type", "key", "rename", "close"):
        sub = subs.add_parser(verb, parents=[common])
        sub.add_argument("target")
        if verb == "read":
            sub.add_argument("--lines", type=int, default=80)
        if verb in {"send", "type"}:
            sub.add_argument("text", help="one quoted literal string; send does not append Enter")
        if verb == "key":
            sub.add_argument("keys", nargs="+")
        if verb == "rename":
            sub.add_argument("new_name")
    json_mode = "--json" in argv
    try:
        values = vars(parser.parse_args(argv))
        json_mode = values.pop("json", False)
        result = dispatch(values)
    except ControlError as exc:
        result = {"schema": SCHEMA, "ok": False, "error": str(exc),
                  "code": exc.code, "exit": EXITS[exc.code]}
    if json_mode:
        print(json.dumps(result, ensure_ascii=True))
    elif not result["ok"]:
        print(f"{result['code']}: {result['error']}", file=sys.stderr)
    elif result["data"]["operation"] == "read" and not result["data"]["dry_run"]:
        print(result["data"]["text"], end="")
    else:
        print(json.dumps(result["data"], ensure_ascii=True))
    return 0 if result["ok"] else result["exit"]


if __name__ == "__main__":
    raise SystemExit(main())
