#!/usr/bin/env python3
"""supervisord event listener: stop the container when a program goes FATAL.

supervisord gives up on a program after `startretries` quick failures and
marks it FATAL, but keeps running itself. On Railway that leaves a container
that looks alive while the DeepTutor API or web server is gone. This listener
sends SIGTERM to supervisord (its parent) instead, so the container exits and
Railway's restart policy starts a fresh one.

Protocol: http://supervisord.org/events.html#event-listener-notification-protocol
stdout is the protocol channel; log lines go to stderr.
"""

from __future__ import annotations

import os
import signal
import sys


def parse_tokens(line: str) -> dict[str, str]:
    pairs = (token.split(":", 1) for token in line.split() if ":" in token)
    return {key: value for key, value in pairs}


def send(text: str) -> None:
    sys.stdout.write(text)
    sys.stdout.flush()


def main() -> int:
    while True:
        send("READY\n")
        header_line = sys.stdin.readline()
        if not header_line:
            return 0
        headers = parse_tokens(header_line)
        payload = sys.stdin.read(int(headers.get("len", "0") or 0))
        send("RESULT 2\nOK")
        if headers.get("eventname") != "PROCESS_STATE_FATAL":
            continue
        program = parse_tokens(payload.split("\n", 1)[0]).get("processname", "?")
        sys.stderr.write(
            f"[railway] supervisord program '{program}' is FATAL (kept failing on start); "
            "stopping the container so Railway restarts it\n"
        )
        sys.stderr.flush()
        os.kill(os.getppid(), signal.SIGTERM)


if __name__ == "__main__":
    sys.exit(main())
