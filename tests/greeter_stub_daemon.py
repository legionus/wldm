#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026  Alexey Gladkov <legion@kernel.org>

"""Run a greeter against a small fake daemon.

This utility is intended for manual greeter development.  It starts a greeter
process with the same inherited socket contract that the real daemon uses, then
answers the daemon-facing greeter protocol without PAM, TTY switching, logind,
or root privileges.
"""

import argparse
import os
import shlex
import socket
import subprocess
import sys
import tempfile
import time

from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import wldm  # pylint: disable=wrong-import-position
import wldm.protocol.greeter as greeter_protocol  # pylint: disable=wrong-import-position


DEFAULT_SESSION_ENTRY = """\
[Desktop Entry]
Type=Application
Name=Stub Session
Comment=Session provided by tests/greeter_stub_daemon.py
Exec=/bin/true
DesktopNames=stub
"""


class StubDaemon:
    def __init__(self, args: argparse.Namespace, sock: socket.socket) -> None:
        self.args = args
        self.sock = sock
        self.username = ""
        self.session_pid = 1000
        self.active_sessions: list[dict[str, object]] = []

    def write_message(self, message: dict[str, Any]) -> None:
        self.sock.sendall(greeter_protocol.encode_message(message))

    def respond(self, request: dict[str, Any], payload: dict[str, Any] | None = None) -> None:
        self.write_message(greeter_protocol.new_response(request, ok=True, payload=payload or {}))

    def reject(self, request: dict[str, Any], code: str, message: str) -> None:
        self.write_message(greeter_protocol.new_error(request, code, message))

    def state_payload(self) -> dict[str, Any]:
        return {
            "seat": self.args.seat,
            "greeter_ready": True,
            "active_sessions": self.active_sessions,
        }

    def handle_create_session(self, request: dict[str, Any]) -> None:
        payload = request.get("payload", {})
        username = payload.get("username", "")
        self.username = secret_to_text(username).strip()

        if self.args.auth == "reject":
            self.reject(request, "auth_failed", "Authentication failed.")
            return

        self.write_message(
            greeter_protocol.new_conversation_response(
                request,
                "pending",
                style=self.args.prompt_style,
                text=self.args.prompt,
            )
        )

    def handle_continue_session(self, request: dict[str, Any]) -> None:
        payload = request.get("payload", {})
        response = secret_to_text(payload.get("response", ""))

        if self.args.auth == "password" and response != self.args.password:
            self.reject(request, "auth_retryable", "Authentication failed.")
            return

        if self.args.auth == "reject":
            self.reject(request, "auth_retryable", "Authentication failed.")
            return

        self.write_message(greeter_protocol.new_conversation_response(request, "ready"))

    def handle_start_session(self, request: dict[str, Any]) -> None:
        payload = request.get("payload", {})
        command = str(payload.get("command", ""))
        desktop_names = list(payload.get("desktop_names", []))

        self.respond(request)
        self.write_message(
            greeter_protocol.new_event(
                greeter_protocol.EVENT_SESSION_STARTING,
                {"command": command, "desktop_names": desktop_names},
            )
        )

        if self.args.session_result == "hang":
            self.active_sessions = [
                {"pid": self.session_pid, "username": self.username, "command": command},
            ]
            self.write_message(greeter_protocol.new_event(greeter_protocol.EVENT_STATE_CHANGED, self.state_payload()))
            return

        time.sleep(self.args.delay)
        failed = self.args.session_result == "failure"
        self.write_message(
            greeter_protocol.new_event(
                greeter_protocol.EVENT_SESSION_FINISHED,
                {
                    "pid": self.session_pid,
                    "returncode": 1 if failed else 0,
                    "failed": failed,
                    "message": "Stub session failed." if failed else "Stub session finished.",
                },
            )
        )

    def handle_request(self, request: dict[str, Any]) -> None:
        action = request.get("action")

        if action == greeter_protocol.ACTION_GET_STATE:
            self.respond(request, self.state_payload())
            return

        if action == greeter_protocol.ACTION_CREATE_SESSION:
            self.handle_create_session(request)
            return

        if action == greeter_protocol.ACTION_CONTINUE_SESSION:
            self.handle_continue_session(request)
            return

        if action == greeter_protocol.ACTION_CANCEL_SESSION:
            self.username = ""
            self.respond(request)
            return

        if action == greeter_protocol.ACTION_START_SESSION:
            self.handle_start_session(request)
            return

        if action in greeter_protocol.CONTROL_ACTIONS:
            self.respond(request, {"accepted": True})
            return

        self.reject(request, "bad_request", f"Unsupported action: {action}")

    def run(self) -> None:
        while True:
            message = greeter_protocol.read_message_socket(self.sock)
            if message is None:
                return

            print(f"<- {message}", flush=True)

            if not greeter_protocol.is_request(message):
                print(f"ignoring non-request message: {message}", file=sys.stderr, flush=True)
                continue

            self.handle_request(message)


def secret_to_text(value: object) -> str:
    if hasattr(value, "as_bytes"):
        data = value.as_bytes()
    elif isinstance(value, (bytes, bytearray, memoryview)):
        data = bytes(value)
    else:
        return str(value)

    return data.decode("utf-8", errors="replace")


def write_default_session(session_dir: str) -> None:
    path = Path(session_dir)
    path.mkdir(parents=True, exist_ok=True)
    (path / "stub.desktop").write_text(DEFAULT_SESSION_ENTRY, encoding="utf-8")


def greeter_argv(args: argparse.Namespace) -> list[str]:
    if args.greeter_command:
        return shlex.split(args.greeter_command)

    return [
        sys.executable,
        "-I",
        "-P",
        str(SRC_DIR / "wldm" / "command.py"),
    ]


def greeter_env(args: argparse.Namespace, socket_fd: int, session_dirs: list[str]) -> dict[str, str]:
    env = os.environ.copy()
    env.update({
        "WLDM_SOURCE_TREE": str(REPO_ROOT),
        "WLDM_ROLE": args.role,
        "WLDM_SOCKET_FD": str(socket_fd),
        "WLDM_DATA_DIR": args.data_dir,
        "WLDM_THEME": args.theme,
        "WLDM_ACTIONS": ":".join(args.actions),
        "WLDM_GREETER_SESSION_DIRS": ":".join(session_dirs),
        "WLDM_GREETER_USER_SESSIONS": "no" if args.no_user_sessions else "yes",
    })

    if args.state_file:
        env["WLDM_STATE_FILE"] = args.state_file

    if args.locale_dir:
        env["WLDM_LOCALE_DIR"] = args.locale_dir

    return env


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a WLDM greeter against a fake daemon protocol peer.",
    )
    parser.add_argument("--role", default="greeter", help="internal greeter role to pass through WLDM_ROLE")
    parser.add_argument("--greeter-command", default="", help="override greeter command; parsed with shell syntax")
    parser.add_argument("--data-dir", default=str(REPO_ROOT / "data"), help="directory that contains greeter resources")
    parser.add_argument("--locale-dir", default="", help="optional locale directory for the greeter")
    parser.add_argument("--theme", default="default", help="greeter theme name")
    parser.add_argument("--session-dir", action="append", default=[], help="Wayland session directory to expose")
    parser.add_argument("--state-file", default="", help="optional greeter state file path")
    parser.add_argument("--no-user-sessions", action="store_true", help="disable per-user session discovery")
    parser.add_argument("--seat", default="seat0", help="seat name returned by get-state")
    parser.add_argument("--actions", default="poweroff,reboot,suspend,hibernate", help="comma-separated power actions")
    parser.add_argument("--auth", choices=("accept", "reject", "password"), default="accept", help="auth result policy")
    parser.add_argument("--password", default="password", help="password required when --auth=password")
    parser.add_argument("--prompt", default="Password:", help="authentication prompt text")
    parser.add_argument("--prompt-style", choices=("secret", "visible", "info", "error"), default="secret")
    parser.add_argument("--session-result", choices=("success", "failure", "hang"), default="success")
    parser.add_argument("--delay", type=float, default=0.5, help="delay before session-finished event")

    args = parser.parse_args(argv)
    args.actions = [item for item in args.actions.split(",") if item]
    return args


def run_with_tempdirs(args: argparse.Namespace) -> int:
    with tempfile.TemporaryDirectory(prefix="wldm-greeter-stub-") as tmpdir:
        session_dirs = list(args.session_dir)
        if not session_dirs:
            session_dir = os.path.join(tmpdir, "sessions")
            write_default_session(session_dir)
            session_dirs.append(session_dir)

        parent_sock, child_sock = socket.socketpair()
        try:
            child_sock.set_inheritable(True)
            proc = subprocess.Popen(
                greeter_argv(args),
                env=greeter_env(args, child_sock.fileno(), session_dirs),
                pass_fds=(child_sock.fileno(),),
            )
            child_sock.close()

            try:
                StubDaemon(args, parent_sock).run()
            except KeyboardInterrupt:
                print("interrupted; stopping greeter", file=sys.stderr)
                proc.terminate()

            return proc.wait()
        finally:
            parent_sock.close()
            child_sock.close()


def main(argv: list[str] | None = None) -> int:
    return run_with_tempdirs(parse_args(sys.argv[1:] if argv is None else argv))


if __name__ == "__main__":
    sys.exit(main())
