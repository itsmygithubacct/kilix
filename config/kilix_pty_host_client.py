#!/usr/bin/python3
"""Constrained host CLI using existing selector/request parsers, no issuance."""
import json
import sys
import kilix_pty as pty
import kilix_pty_selector as selector
import kilix_pty_request as request
from kilix_pty_agent_client import RemoteController


def main(argv):
    if argv[:1] != ["pty"]:
        raise ValueError("only pty routes are available")
    argv = argv[1:]
    if argv in ([], ["help"], ["--help"]):
        print("Constrained successor: pty resolve|end --selector-json JSON [--operation-id KEY] --json; "
              "pty request --request-json -|FILE. User-issued original selection required. "
              "No raw administrative operations. No CLI flag issues authority.")
        return 0
    # Only supplies standard validation-failure envelopes; never used for transport.
    broker = pty.Broker("/unavailable", "/unavailable", "0.3", 5)
    controller = RemoteController()
    if argv[0] in ("resolve", "end"):
        return selector.command(broker, argv[0], argv[1:], controller=controller)
    if argv[0] == "request":
        return request.cmd_request(broker, argv[1:], controller=controller)
    raise ValueError("use selected end/resolve or validated request; unsupported constrained host command")


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except (ValueError, OSError, KeyError) as error:
        print(json.dumps({"result":"refused", "reason":"client_failure", "message":str(error)}))
        raise SystemExit(3)
