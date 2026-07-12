# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026  Alexey Gladkov <legion@kernel.org>

import argparse
import socket

import wldm.protocol.greeter as greeter_protocol
from wldm.secret import SecretBytes

import tests.greeter_stub_daemon as stub


def make_args(**overrides):
    values = {
        "actions": ["poweroff", "reboot"],
        "auth": "accept",
        "data_dir": "/srv/wldm/data",
        "delay": 0.0,
        "greeter_command": "",
        "locale_dir": "",
        "no_user_sessions": False,
        "password": "secret",
        "prompt": "Password:",
        "prompt_style": "secret",
        "reexec_after_start": False,
        "role": "greeter",
        "seat": "seat0",
        "session_result": "success",
        "state_file": "",
        "theme": "default",
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def read_message(sock):
    message = greeter_protocol.read_message_socket(sock)
    assert message is not None
    return message


def test_secret_to_text_reads_secret_bytes():
    assert stub.secret_to_text(SecretBytes("secret")) == "secret"


def test_greeter_env_sets_daemon_contract():
    args = make_args()

    env = stub.greeter_env(args, 7, ["/tmp/sessions"])

    assert env["WLDM_ROLE"] == "greeter"
    assert env["WLDM_SOCKET_FD"] == "7"
    assert env["WLDM_DATA_DIR"] == "/srv/wldm/data"
    assert env["WLDM_GREETER_SESSION_DIRS"] == "/tmp/sessions"
    assert env["WLDM_ACTIONS"] == "poweroff:reboot"
    assert "WLDM_STATE_FILE" not in env


def test_greeter_env_sets_explicit_state_file():
    args = make_args(state_file="/tmp/state")

    env = stub.greeter_env(args, 7, ["/tmp/sessions"])

    assert env["WLDM_STATE_FILE"] == "/tmp/state"


def test_stub_daemon_accepts_password_flow():
    left, right = socket.socketpair()
    try:
        daemon = stub.StubDaemon(make_args(auth="password", password="secret"), left)
        create = greeter_protocol.new_request(greeter_protocol.ACTION_CREATE_SESSION, {"username": "alice"})
        daemon.handle_request(create)

        create_answer = read_message(right)
        assert create_answer["ok"] is True
        assert create_answer["payload"] == {
            "state": "pending",
            "message": {"style": "secret", "text": "Password:"},
        }

        cont = greeter_protocol.new_request(
            greeter_protocol.ACTION_CONTINUE_SESSION,
            {"response": SecretBytes("secret")},
        )
        daemon.handle_request(cont)

        continue_answer = read_message(right)
        assert continue_answer["ok"] is True
        assert continue_answer["payload"] == {"state": "ready"}
    finally:
        left.close()
        right.close()


def test_stub_daemon_rejects_wrong_password():
    left, right = socket.socketpair()
    try:
        daemon = stub.StubDaemon(make_args(auth="password", password="secret"), left)
        cont = greeter_protocol.new_request(
            greeter_protocol.ACTION_CONTINUE_SESSION,
            {"response": SecretBytes("wrong")},
        )
        daemon.handle_request(cont)

        answer = read_message(right)
        assert answer["ok"] is False
        assert answer["error"]["code"] == "auth_retryable"
    finally:
        left.close()
        right.close()


def test_stub_daemon_sends_session_events():
    left, right = socket.socketpair()
    try:
        daemon = stub.StubDaemon(make_args(session_result="success", delay=0.0), left)
        start = greeter_protocol.new_request(
            greeter_protocol.ACTION_START_SESSION,
            {
                "command": "sway",
                "desktop_names": ["sway"],
                "name": "Sway",
                "icon": "",
                "desktop_file": "",
            },
        )
        daemon.handle_request(start)

        assert read_message(right)["ok"] is True
        starting = read_message(right)
        assert starting["event"] == greeter_protocol.EVENT_SESSION_STARTING
        finished = read_message(right)
        assert finished["event"] == greeter_protocol.EVENT_SESSION_FINISHED
        assert finished["payload"]["failed"] is False
    finally:
        left.close()
        right.close()


def test_stub_daemon_can_send_reexec_after_start():
    left, right = socket.socketpair()
    try:
        daemon = stub.StubDaemon(make_args(reexec_after_start=True, session_result="hang"), left)
        start = greeter_protocol.new_request(
            greeter_protocol.ACTION_START_SESSION,
            {
                "command": "sway",
                "desktop_names": ["sway"],
                "name": "Sway",
                "icon": "",
                "desktop_file": "",
            },
        )
        daemon.handle_request(start)

        assert read_message(right)["ok"] is True
        assert read_message(right)["event"] == greeter_protocol.EVENT_SESSION_STARTING
        assert read_message(right)["event"] == greeter_protocol.EVENT_REEXEC
    finally:
        left.close()
        right.close()
