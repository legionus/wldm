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


class DummyService:
    def __init__(self):
        self.snapshots = []
        self.closed = False

    def update_state(self, snapshot):
        self.snapshots.append(snapshot)

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


def test_seat_object_path_normalizes_seat_name():
    assert wldm.dbus_adapter.seat_object_path("seat0") == "/org/freedesktop/DisplayManager/Seat0"
    assert wldm.dbus_adapter.seat_object_path("my-seat") == "/org/freedesktop/DisplayManager/Seatmy_seat"


def test_session_paths_follow_active_session_pids():
    snapshot = {
        "seat": "seat0",
        "active_sessions": [
            {"pid": 101, "username": "alice", "command": "sway"},
            {"pid": 202, "username": "bob", "command": "labwc"},
        ],
    }

    assert wldm.dbus_adapter.session_paths(snapshot) == [
        "/org/freedesktop/DisplayManager/Session101",
        "/org/freedesktop/DisplayManager/Session202",
    ]


def test_schedule_state_update_updates_service_once():
    service = DummyService()
    snapshot = {"seat": "seat0", "active_sessions": []}

    assert wldm.dbus_adapter.schedule_state_update(service, snapshot) is False
    assert service.snapshots == [snapshot]


def test_read_daemon_events_applies_state_changes(monkeypatch):
    calls = []
    snapshot = {
        "seat": "seat0",
        "greeter_ready": True,
        "last_username": "alice",
        "last_session_command": "sway",
        "active_sessions": [],
    }
    client = DummyClient([
        wldm.protocol.new_event(wldm.protocol.EVENT_STATE_CHANGED, snapshot),
        None,
    ])
    service = DummyService()

    class DummyGLib:
        @staticmethod
        def idle_add(func, *args):
            calls.append((func, args))
            func(*args)
            return 1

    class DummyLoop:
        def __init__(self):
            self.quit_calls = 0

        def quit(self):
            self.quit_calls += 1

    loop = DummyLoop()

    wldm.dbus_adapter.read_daemon_events(client, service, DummyGLib, loop)

    assert service.snapshots == [snapshot]
    assert loop.quit_calls == 1


def test_run_adapter_drops_privileges_and_runs_loop(monkeypatch):
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
    ])
    service = DummyService()

    class DummyLoop:
        def run(self):
            calls["loop_run"] = True

        def quit(self):
            calls["loop_quit"] = True

    class DummyGLib:
        @staticmethod
        def MainLoop():
            return DummyLoop()

    class DummyThread:
        def __init__(self, target, args, daemon):
            calls["thread_args"] = (target, args, daemon)

        def start(self):
            calls["thread_started"] = True

        def join(self, timeout):
            calls["thread_join"] = timeout

    monkeypatch.setattr(wldm.dbus_adapter, "SocketClient", lambda fd: calls.update({"fd": fd}) or client)
    monkeypatch.setattr(wldm.dbus_adapter, "adapter_ipc_fd", lambda: 13)
    monkeypatch.setattr(wldm.dbus_adapter, "load_dbus_modules", lambda: ("gio", DummyGLib))
    monkeypatch.setattr(
        wldm.dbus_adapter,
        "DisplayManagerService",
        lambda service_name, snapshot, Gio, GLib: calls.update(
            {"service": service_name, "snapshot": snapshot, "gio": Gio, "glib": GLib}
        ) or service,
    )
    monkeypatch.setattr(wldm.dbus_adapter.wldm.protocol, "new_request", lambda action, payload: dict(request))
    monkeypatch.setattr(
        wldm.dbus_adapter.wldm,
        "drop_privileges",
        lambda username, uid, gid, workdir: calls.update(
            {"drop_privileges": (username, uid, gid, workdir)}
        ),
    )
    monkeypatch.setattr(wldm.dbus_adapter.threading, "Thread", DummyThread)

    result = wldm.dbus_adapter.run_adapter("gdm", 32, 32, "/var/lib/gdm", "org.example.DisplayManager")

    assert result == wldm.dbus_adapter.wldm.EX_SUCCESS
    assert calls["fd"] == 13
    assert calls["service"] == "org.example.DisplayManager"
    assert calls["gio"] == "gio"
    assert calls["drop_privileges"] == ("gdm", 32, 32, "/var/lib/gdm")
    assert calls["loop_run"] is True
    assert calls["thread_started"] is True
    assert calls["thread_join"] == 1.0
    assert service.closed is True
    assert client.closed is True


def test_cmd_main_runs_adapter(monkeypatch):
    pw = pwd.struct_passwd(("gdm", "x", 32, 32, "", "/var/lib/gdm", "/bin/false"))
    calls = {}

    monkeypatch.setattr(wldm.dbus_adapter.pwd, "getpwnam", lambda username: pw)
    monkeypatch.setattr(
        wldm.dbus_adapter,
        "run_adapter",
        lambda username, uid, gid, workdir, service: calls.update(
            {"adapter": (username, uid, gid, workdir, service)}
        ) or wldm.dbus_adapter.wldm.EX_SUCCESS,
    )

    result = wldm.dbus_adapter.cmd_main(
        SimpleNamespace(username="gdm", service="org.freedesktop.DisplayManager")
    )

    assert result == wldm.dbus_adapter.wldm.EX_SUCCESS
    assert calls["adapter"] == ("gdm", 32, 32, "/var/lib/gdm", "org.freedesktop.DisplayManager")
