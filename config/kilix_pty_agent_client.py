#!/usr/bin/python3
"""Untrusted sandbox client. The socket grants only controller-checked dispatch."""
import json
import socket
import sys


def call(document):
    raw = json.dumps(document, ensure_ascii=True, separators=(",", ":")).encode() + b"\n"
    if len(raw) > 16384:
        raise ValueError("request exceeds 16 KiB")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(12)
        connection.connect("/run/dispatch.sock")
        connection.sendall(raw)
        with connection.makefile("rb") as stream:
            reply = stream.readline(65537)
        if not reply.endswith(b"\n") or len(reply) > 65536:
            raise ValueError("invalid controller response")
    return json.loads(reply)


class RemoteController:
    def dispatch(self, request):
        response = call({"route": "selector", "request": request})
        return response["code"], response["receipt"]

    def read(self, request):
        response = call({"route": "read", "request": request})
        return response["code"], response["receipt"]

    def read_cli(self, argv):
        return call({"route": "host_read", "argv": argv})


def main(argv):
    response = call({"route": "broker", "argv": argv})
    if "stdout" in response:
        sys.stdout.write(response["stdout"])
    else:
        print(json.dumps(response["receipt"], ensure_ascii=True, separators=(",", ":")))
    return response["code"]


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except (OSError, ValueError, KeyError) as error:
        print(json.dumps({"result": "refused", "reason": "controller_unavailable", "message": str(error)}))
        raise SystemExit(3)
