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


def test_name_lifecycle_callbacks_update_service_state():
    calls = []
    service = wldm.dbus_adapter.DisplayManagerService.__new__(wldm.dbus_adapter.DisplayManagerService)
    service.name_acquired = False
    service.loop = object()
    service.GLib = SimpleNamespace(idle_add=lambda func, arg: calls.append((func, arg)))

    service._on_name_acquired(None, "org.freedesktop.DisplayManager")

    assert service.name_acquired is True

    service._on_name_lost(None, "org.freedesktop.DisplayManager")

    assert service.name_acquired is False
    assert calls == [(wldm.dbus_adapter.schedule_loop_quit, service.loop)]


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


def test_adapter_ipc_fd_requires_environment_variable(monkeypatch):
    monkeypatch.delenv("WLDM_SOCKET_FD", raising=False)

    try:
        wldm.dbus_adapter.adapter_ipc_fd()
    except RuntimeError as exc:
        assert "WLDM_SOCKET_FD" in str(exc)
    else:
        raise AssertionError("adapter_ipc_fd() should require the inherited fd")


def test_adapter_ipc_fd_marks_fd_inheritable(monkeypatch):
    calls = []
    monkeypatch.setenv("WLDM_SOCKET_FD", "17")
    monkeypatch.setattr(wldm.dbus_adapter.os, "set_inheritable", lambda fd, value: calls.append((fd, value)))

    assert wldm.dbus_adapter.adapter_ipc_fd() == 17
    assert calls == [(17, True)]


def test_request_state_rejects_bad_responses(monkeypatch):
    request = {"v": 1, "id": "req-1", "type": "request", "action": wldm.protocol.ACTION_GET_STATE, "payload": {}}
    monkeypatch.setattr(wldm.dbus_adapter.wldm.protocol, "new_request", lambda action, payload: dict(request))

    for response in (
        None,
        {"v": 1, "type": "event", "event": "x", "payload": {}},
        wldm.protocol.new_response(request, ok=False, error={"code": "x", "message": "no"}),
        wldm.protocol.new_response(request, ok=True, payload=[]),
    ):
        client = DummyClient([response])

        try:
            wldm.dbus_adapter.request_state(client)
        except RuntimeError:
            pass
        else:
            raise AssertionError("request_state() should reject malformed responses")


def test_read_daemon_events_ignores_non_state_events():
    snapshot = {"seat": "seat0", "greeter_ready": True, "active_sessions": []}
    client = DummyClient([
        wldm.protocol.new_event(wldm.protocol.EVENT_SESSION_STARTING, {"command": "sway", "desktop_names": []}),
        wldm.protocol.new_event(wldm.protocol.EVENT_SESSION_FINISHED, {"pid": 1, "returncode": 0, "failed": False, "message": ""}),
        None,
    ])
    service = DummyService()

    class DummyGLib:
        @staticmethod
        def idle_add(func, *args):
            func(*args)
            return 1

    class DummyLoop:
        def __init__(self):
            self.quit_calls = 0

        def quit(self):
            self.quit_calls += 1

    loop = DummyLoop()

    wldm.dbus_adapter.read_daemon_events(client, service, DummyGLib, loop)

    assert service.snapshots == []
    assert loop.quit_calls == 1


def test_schedule_loop_quit_returns_false():
    calls = []
    loop = SimpleNamespace(quit=lambda: calls.append("quit"))

    assert wldm.dbus_adapter.schedule_loop_quit(loop) is False
    assert calls == ["quit"]


def test_display_manager_service_helpers_cover_properties_and_methods():
    signals = []
    registered = []
    unregistered = []

    class DummyConnection:
        def register_object(self, path, interface_info, method_call, get_property, user_data):
            registered.append(path)
            return len(registered)

        def unregister_object(self, reg_id):
            unregistered.append(reg_id)

        def emit_signal(self, *args):
            signals.append(args)

    class DummyDBusNodeInfo:
        @staticmethod
        def new_for_xml(xml):
            return SimpleNamespace(interfaces=[object()])

    class DummyGio:
        BusType = SimpleNamespace(SYSTEM=1)
        BusNameOwnerFlags = SimpleNamespace(NONE=0)
        DBusNodeInfo = DummyDBusNodeInfo

        @staticmethod
        def bus_get_sync(bus_type, cancellable):
            return DummyConnection()

        @staticmethod
        def bus_own_name_on_connection(connection, service, flags, acquired, lost):
            return 77

        @staticmethod
        def bus_unown_name(owner_id):
            unregistered.append(("owner", owner_id))

    class DummyGLib:
        @staticmethod
        def Variant(signature, value):
            return (signature, value)

    snapshot = {
        "seat": "seat0",
        "active_sessions": [{"pid": 11, "username": "alice", "command": "sway"}],
    }
    service = wldm.dbus_adapter.DisplayManagerService("org.test.DisplayManager", snapshot, DummyGio, DummyGLib)

    assert service.manager_path() == wldm.dbus_adapter.MANAGER_PATH
    assert service.current_seat_path().endswith("/Seat0")
    assert service.session_entry("/org/freedesktop/DisplayManager/Session11")["username"] == "alice"
    assert service._manager_property("Seats") == ("ao", [service.current_seat_path()])
    assert service._seat_property("Id") == ("s", "seat0")
    assert service._session_property("/org/freedesktop/DisplayManager/Session11", "Username") == ("s", "alice")
    assert service._on_get_property(None, "", service.manager_path(), wldm.dbus_adapter.MANAGER_INTERFACE, "Sessions") == (
        "ao", ["/org/freedesktop/DisplayManager/Session11"]
    )

    returned = []
    invocation = SimpleNamespace(
        return_value=lambda value: returned.append(("value", value)),
        return_dbus_error=lambda name, message: returned.append(("error", name, message)),
    )
    service._on_method_call(None, "", service.manager_path(), wldm.dbus_adapter.MANAGER_INTERFACE, "ListSeats", None, invocation)
    service._on_method_call(None, "", service.manager_path(), wldm.dbus_adapter.MANAGER_INTERFACE, "ListSessions", None, invocation)
    service._on_method_call(None, "", service.manager_path(), wldm.dbus_adapter.MANAGER_INTERFACE, "Nope", None, invocation)

    assert returned[0] == ("value", ("(ao)", ([service.current_seat_path()],)))
    assert returned[1] == ("value", ("(ao)", (["/org/freedesktop/DisplayManager/Session11"],)))
    assert returned[2][0] == "error"

    service.update_state({"seat": "seat1", "active_sessions": []})
    assert signals

    service.close()
    assert ("owner", 77) in unregistered


def test_session_entry_rejects_missing_or_malformed_sessions():
    service = wldm.dbus_adapter.DisplayManagerService.__new__(wldm.dbus_adapter.DisplayManagerService)

    service.snapshot = {"active_sessions": "bad"}
    try:
        service.session_entry("/org/freedesktop/DisplayManager/Session1")
    except KeyError:
        pass
    else:
        raise AssertionError("session_entry() should reject malformed session lists")

    service.snapshot = {"active_sessions": [{"pid": 2}]}
    try:
        service.session_entry("/org/freedesktop/DisplayManager/Session1")
    except KeyError:
        pass
    else:
        raise AssertionError("session_entry() should reject missing session paths")


def test_on_get_property_rejects_unknown_interface():
    service = wldm.dbus_adapter.DisplayManagerService.__new__(wldm.dbus_adapter.DisplayManagerService)

    try:
        service._on_get_property(None, "", "/", "org.example.Unknown", "Id")
    except KeyError as exc:
        assert "org.example.Unknown" in str(exc)
    else:
        raise AssertionError("_on_get_property() should reject unknown interfaces")


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
    monkeypatch.setattr(wldm.dbus_adapter, "load_unprivileged_modules", lambda: ("gio", DummyGLib))
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

    monkeypatch.setenv("WLDM_DBUS_LOG_PATH", "/tmp/wldm/dbus.log")
    monkeypatch.setattr(wldm.dbus_adapter.pwd, "getpwnam", lambda username: pw)
    monkeypatch.setattr(
        wldm.dbus_adapter.wldm,
        "setup_file_logger",
        lambda logger_arg, level, fmt, path: calls.update({"logger": (logger_arg, level, fmt, path)}) or logger_arg,
    )
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
    assert calls["logger"][3] == "/tmp/wldm/dbus.log"
    assert calls["adapter"] == ("gdm", 32, 32, "/var/lib/gdm", "org.freedesktop.DisplayManager")
