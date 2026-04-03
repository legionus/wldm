# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026  Alexey Gladkov <legion@kernel.org>

from types import SimpleNamespace
import pwd

import wldm.dbus_adapter
import wldm.protocol


class DummyClient:
    def __init__(self, messages):
        self.messages = iter(messages)
        self.written = []
        self.closed = False

    def write_message(self, message):
        self.written.append(message)

    def read_message(self):
        return next(self.messages, None)

    def close(self):
        self.closed = True


def test_request_state_reads_valid_snapshot(monkeypatch):
    request = {"v": 1, "id": "req-1", "type": "request", "action": wldm.protocol.ACTION_GET_STATE, "payload": {}}
    monkeypatch.setattr(wldm.dbus_adapter.wldm.protocol, "new_request", lambda action, payload: dict(request))

    client = DummyClient([
        wldm.protocol.new_response(
            request,
            ok=True,
            payload={
                "seat": "seat0",
                "greeter_ready": False,
                "last_username": "",
                "last_session_command": "",
                "active_sessions": [],
            },
        ),
    ])

    payload = wldm.dbus_adapter.request_state(client)

    assert client.written == [request]
    assert payload["seat"] == "seat0"


def test_run_adapter_drops_privileges_and_consumes_state_events(monkeypatch):
    calls = {}
    request = {"v": 1, "id": "req-1", "type": "request", "action": wldm.protocol.ACTION_GET_STATE, "payload": {}}
    client = DummyClient([
        wldm.protocol.new_response(
            request,
            ok=True,
            payload={
                "seat": "seat0",
                "greeter_ready": True,
                "last_username": "alice",
                "last_session_command": "sway",
                "active_sessions": [],
            },
        ),
        wldm.protocol.new_event(
            wldm.protocol.EVENT_STATE_CHANGED,
            {
                "seat": "seat0",
                "greeter_ready": True,
                "last_username": "alice",
                "last_session_command": "sway",
                "active_sessions": [],
            },
        ),
        None,
    ])

    monkeypatch.setattr(wldm.dbus_adapter, "SocketClient", lambda fd: calls.update({"fd": fd}) or client)
    monkeypatch.setattr(wldm.dbus_adapter, "adapter_ipc_fd", lambda: 13)
    monkeypatch.setattr(wldm.dbus_adapter.wldm.protocol, "new_request", lambda action, payload: dict(request))
    monkeypatch.setattr(
        wldm.dbus_adapter.wldm,
        "drop_privileges",
        lambda username, uid, gid, workdir: calls.update(
            {"drop_privileges": (username, uid, gid, workdir)}
        ),
    )

    result = wldm.dbus_adapter.run_adapter("gdm", 32, 32, "/var/lib/gdm")

    assert result == wldm.dbus_adapter.wldm.EX_SUCCESS
    assert calls["fd"] == 13
    assert calls["drop_privileges"] == ("gdm", 32, 32, "/var/lib/gdm")
    assert client.closed is True


def test_cmd_main_runs_adapter(monkeypatch):
    pw = pwd.struct_passwd(("gdm", "x", 32, 32, "", "/var/lib/gdm", "/bin/false"))
    calls = {}

    monkeypatch.setattr(wldm.dbus_adapter.pwd, "getpwnam", lambda username: pw)
    monkeypatch.setattr(
        wldm.dbus_adapter,
        "run_adapter",
        lambda username, uid, gid, workdir: calls.update(
            {"adapter": (username, uid, gid, workdir)}
        ) or wldm.dbus_adapter.wldm.EX_SUCCESS,
    )

    result = wldm.dbus_adapter.cmd_main(SimpleNamespace(username="gdm"))

    assert result == wldm.dbus_adapter.wldm.EX_SUCCESS
    assert calls["adapter"] == ("gdm", 32, 32, "/var/lib/gdm")
