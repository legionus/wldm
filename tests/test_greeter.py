# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026  Alexey Gladkov <legion@kernel.org>

import importlib
import pwd
import sys
import types


def load_greeter_module(monkeypatch):
    timeout_calls = []

    class FakeBuilderInstance:
        def __init__(self):
            self.translation_domain = None
            self.loaded_path = None

        def set_translation_domain(self, domain):
            self.translation_domain = domain

        def add_from_file(self, path):
            self.loaded_path = path

        def get_object(self, name):
            return None

    class FakeBuilderClass:
        @staticmethod
        def new():
            return FakeBuilderInstance()

    class FakeApplication:
        def __init__(self, application_id=None, flags=None):
            self.application_id = application_id
            self.flags = flags
            self.connections = []
            self.run_called = False
            self.quit_called = False

        def connect(self, signal, callback):
            self.connections.append((signal, callback))

        def run(self):
            self.run_called = True

        def quit(self):
            self.quit_called = True

    class FakeStringList:
        def __init__(self):
            self.items = []

        def append(self, value):
            self.items.append(value)

    class FakeCssProvider:
        def __init__(self):
            self.loaded_paths = []

        def load_from_path(self, path):
            self.loaded_paths.append(path)

    fake_gtk = types.SimpleNamespace(
        Application=FakeApplication,
        Builder=FakeBuilderClass,
        StringList=FakeStringList,
        CssProvider=FakeCssProvider,
        StyleContext=types.SimpleNamespace(add_provider_for_display=lambda *args, **kwargs: None),
        STYLE_PROVIDER_PRIORITY_APPLICATION=1,
    )
    fake_gdk = types.SimpleNamespace(Display=types.SimpleNamespace(get_default=lambda: None))
    fake_gio = types.SimpleNamespace(
        ApplicationFlags=types.SimpleNamespace(FLAGS_NONE=0),
    )
    fake_glib = types.SimpleNamespace(timeout_add_seconds=lambda interval, callback: timeout_calls.append((interval, callback)) or 1)
    fake_repository = types.SimpleNamespace(Gtk=fake_gtk, Gdk=fake_gdk, Gio=fake_gio, GLib=fake_glib)
    fake_gi = types.SimpleNamespace(
        require_version=lambda *args, **kwargs: None,
        repository=fake_repository,
    )

    monkeypatch.setitem(sys.modules, "gi", fake_gi)
    monkeypatch.setitem(sys.modules, "gi.repository", fake_repository)
    sys.modules.pop("wldm.greeter", None)

    module = importlib.import_module("wldm.greeter")
    module._test_timeout_calls = timeout_calls  # type: ignore[attr-defined]
    return module


class DummyClient:
    def write_message(self, message):
        return None

    def read_message(self):
        return None

    def can_read(self):
        return False

    def close(self):
        return None


def test_desktop_sessions_filters_and_sorts_entries(monkeypatch):
    greeter = load_greeter_module(monkeypatch)

    class FakeEntry:
        def __init__(self, name):
            self.name = name

        def is_file(self):
            return True

    class FakeScandir:
        def __enter__(self):
            return iter([FakeEntry("b.desktop"), FakeEntry("ignored.txt"), FakeEntry("a.desktop")])

        def __exit__(self, exc_type, exc, tb):
            return False

    class FakeConfig:
        def read(self, path):
            self.path = path

        def get(self, section, option, fallback=""):
            base = self.path.split("/")[-1]
            data = {
                "a.desktop": {
                    "type": "Application", "name": "Alpha", "exec": "alpha",
                    "comment": "Alpha session", "DesktopNames": "AlphaDesktop;WL;",
                },
                "b.desktop": {"type": "Application", "name": "Beta", "exec": "beta", "comment": "Beta session"},
            }
            return data.get(base, {}).get(option, fallback)

    monkeypatch.setattr(greeter.wldm.sessions.os, "scandir", lambda path: FakeScandir())
    monkeypatch.setattr(greeter.wldm.sessions.configparser, "ConfigParser", FakeConfig)

    assert greeter.wldm.sessions.desktop_sessions() == [
        {"name": "Alpha", "command": "alpha", "comment": "Alpha session", "desktop_names": ["AlphaDesktop", "WL"]},
        {"name": "Beta", "command": "beta", "comment": "Beta session", "desktop_names": ["b"]},
    ]


def test_session_data_dirs_prepends_user_directory(monkeypatch):
    greeter = load_greeter_module(monkeypatch)
    pw = pwd.struct_passwd(("alice", "x", 1000, 1000, "", "/home/alice", "/bin/sh"))

    monkeypatch.setenv("WLDM_GREETER_USER_SESSIONS", "yes")
    monkeypatch.setenv("WLDM_GREETER_SESSION_DIRS", "/usr/share/wayland-sessions:/opt/wayland-sessions")
    monkeypatch.setenv("WLDM_GREETER_USER_SESSION_DIR", ".config/wldm/sessions")
    monkeypatch.setattr(greeter.wldm.sessions.pwd, "getpwnam", lambda username: pw)

    assert greeter.wldm.sessions.session_data_dirs("alice") == [
        "/home/alice/.config/wldm/sessions",
        "/usr/share/wayland-sessions",
        "/opt/wayland-sessions",
    ]


def test_session_data_dirs_can_disable_user_sessions(monkeypatch):
    greeter = load_greeter_module(monkeypatch)

    monkeypatch.setenv("WLDM_GREETER_USER_SESSIONS", "no")
    monkeypatch.setenv("WLDM_GREETER_SESSION_DIRS", "/usr/share/wayland-sessions:/opt/wayland-sessions")

    assert greeter.wldm.sessions.session_data_dirs("alice") == [
        "/usr/share/wayland-sessions",
        "/opt/wayland-sessions",
    ]


def test_available_actions_reads_environment(monkeypatch):
    greeter = load_greeter_module(monkeypatch)

    monkeypatch.setenv("WLDM_ACTIONS", "poweroff:reboot:suspend")

    assert greeter.available_actions() == {"poweroff", "reboot", "suspend"}


def test_login_app_uses_project_application_id(monkeypatch):
    greeter = load_greeter_module(monkeypatch)

    monkeypatch.setattr(greeter.wldm.sessions, "desktop_sessions", lambda username="": [])

    app = greeter.LoginApp(client=DummyClient())

    assert app.app.application_id == "org.wldm.greeter"


def test_parse_desktop_names_splits_semicolon_list(monkeypatch):
    greeter = load_greeter_module(monkeypatch)

    assert greeter.wldm.sessions.parse_desktop_names("GNOME;GNOME-Classic;") == ["GNOME", "GNOME-Classic"]


def test_desktop_sessions_merge_user_entries_before_system(monkeypatch):
    greeter = load_greeter_module(monkeypatch)
    scanned_paths = []

    class FakeEntry:
        def __init__(self, name):
            self.name = name

        def is_file(self):
            return True

    class FakeScandir:
        def __init__(self, entries):
            self.entries = entries

        def __enter__(self):
            return iter(self.entries)

        def __exit__(self, exc_type, exc, tb):
            return False

    class FakeConfig:
        def read(self, path):
            self.path = path

        def get(self, section, option, fallback=""):
            base = self.path.split("/")[-1]
            data = {
                "user.desktop": {"type": "Application", "name": "Sway", "exec": "sway --debug", "comment": "User sway"},
                "system.desktop": {"type": "Application", "name": "Sway", "exec": "sway", "comment": "System sway"},
                "labwc.desktop": {"type": "Application", "name": "Labwc", "exec": "labwc", "comment": "Labwc"},
            }
            return data.get(base, {}).get(option, fallback)

    monkeypatch.setattr(greeter.wldm.sessions, "session_data_dirs",
                        lambda username="": ["/home/alice/.local/share/wayland-sessions", "/usr/share/wayland-sessions"])

    def fake_scandir(path):
        scanned_paths.append(path)
        if path.startswith("/home/alice"):
            return FakeScandir([FakeEntry("user.desktop")])
        return FakeScandir([FakeEntry("system.desktop"), FakeEntry("labwc.desktop")])

    monkeypatch.setattr(greeter.wldm.sessions.os, "scandir", fake_scandir)
    monkeypatch.setattr(greeter.wldm.sessions.configparser, "ConfigParser", FakeConfig)

    assert greeter.wldm.sessions.desktop_sessions("alice") == [
        {"name": "Labwc", "command": "labwc", "comment": "Labwc", "desktop_names": ["labwc"]},
        {"name": "Sway", "command": "sway --debug", "comment": "User sway", "desktop_names": ["user"]},
    ]
    assert scanned_paths == [
        "/home/alice/.local/share/wayland-sessions",
        "/usr/share/wayland-sessions",
    ]


def test_desktop_sessions_handles_oserror(monkeypatch):
    greeter = load_greeter_module(monkeypatch)

    monkeypatch.setattr(
        greeter.wldm.sessions.os,
        "scandir",
        lambda path: (_ for _ in ()).throw(OSError("boom")),
    )

    assert greeter.wldm.sessions.desktop_sessions() == []


def test_get_session_command_returns_selected_command(monkeypatch):
    greeter = load_greeter_module(monkeypatch)

    class FakeItem:
        def get_string(self):
            return "Beta"

    app = greeter.LoginApp.__new__(greeter.LoginApp)
    app.sessions = [
        {"name": "Alpha", "command": "alpha", "comment": "Alpha session", "desktop_names": ["alpha"]},
        {"name": "Beta", "command": "beta --flag", "comment": "Beta session", "desktop_names": ["beta"]},
    ]
    app.sessions_entry = types.SimpleNamespace(get_selected_item=lambda: FakeItem())

    assert greeter.LoginApp.get_session_command(app) == "beta --flag"


def test_get_session_command_handles_missing_selection(monkeypatch):
    greeter = load_greeter_module(monkeypatch)

    app = greeter.LoginApp.__new__(greeter.LoginApp)
    app.sessions = [{"name": "Alpha", "command": "alpha", "comment": "Alpha session", "desktop_names": ["alpha"]}]
    app.sessions_entry = types.SimpleNamespace(get_selected_item=lambda: None)

    assert greeter.LoginApp.get_session_command(app) == ""


def test_account_service_profile_reads_real_name(monkeypatch, tmp_path):
    greeter = load_greeter_module(monkeypatch)
    profile_dir = tmp_path / "AccountsService" / "users"
    profile_dir.mkdir(parents=True)
    profile_file = profile_dir / "alice"
    profile_file.write_text("[User]\nRealName=Alice Doe\nIcon=/missing/icon.png\n", encoding="utf-8")

    monkeypatch.setattr(greeter.os.path, "isfile", lambda path: path in [str(profile_file)])
    monkeypatch.setattr(greeter.os.path, "join", lambda *parts: str(profile_file) if parts[-1] == "alice" else "/".join(parts))

    profile = greeter.account_service_profile("alice")

    assert profile["display_name"] == "Alice Doe"
    assert profile["avatar_path"] == ""


def test_login_app_run_calls_application_run(monkeypatch):
    greeter = load_greeter_module(monkeypatch)

    monkeypatch.setattr(greeter.wldm.sessions, "desktop_sessions", lambda username="": [])

    app = greeter.LoginApp(client=DummyClient())
    app.run()

    assert app.app.run_called is True


def test_update_clock_sets_date_and_time(monkeypatch):
    greeter = load_greeter_module(monkeypatch)

    class FakeLabel:
        def __init__(self):
            self.text = None

        def set_text(self, text):
            self.text = text

    app = greeter.LoginApp.__new__(greeter.LoginApp)
    app.date_label = FakeLabel()
    app.time_label = FakeLabel()
    monkeypatch.setattr(greeter.time, "strftime",
                        lambda fmt: {"%A, %d %B": "Monday, 30 March", "%H:%M": "09:45"}[fmt])

    greeter.LoginApp.update_clock(app)

    assert app.date_label.text == "Monday, 30 March"
    assert app.time_label.text == "09:45"


def test_send_recv_answer_round_trips_protocol_messages(monkeypatch):
    greeter = load_greeter_module(monkeypatch)

    monkeypatch.setattr(greeter.wldm.sessions, "desktop_sessions", lambda username="": [])

    class FakeClient:
        def __init__(self, messages):
            self.messages = iter(messages)
            self.sent = []
            self.readable = False

        def write_message(self, message):
            self.sent.append(message)

        def read_message(self):
            return next(self.messages, None)

        def can_read(self):
            return self.readable

        def close(self):
            return None

    request = greeter.wldm.protocol.new_request(greeter.wldm.protocol.ACTION_REBOOT, {})
    client = FakeClient([
        {
            "v": 1,
            "type": "event",
            "event": greeter.wldm.protocol.EVENT_SESSION_FINISHED,
            "payload": {"pid": 1, "returncode": 0},
        },
        {
            "v": 1,
            "id": request["id"],
            "type": "response",
            "action": greeter.wldm.protocol.ACTION_REBOOT,
            "ok": True,
            "payload": {"accepted": True},
        },
    ])
    app = greeter.LoginApp(client=client)

    answer = app.send_recv_answer(request)

    assert answer == {
        "v": 1,
        "id": request["id"],
        "type": "response",
        "action": greeter.wldm.protocol.ACTION_REBOOT,
        "ok": True,
        "payload": {"accepted": True},
    }
    sent = client.sent[0]
    assert sent["v"] == 1
    assert sent["type"] == "request"
    assert sent["action"] == greeter.wldm.protocol.ACTION_REBOOT
    assert sent["payload"] == {}


def test_read_password_secret_uses_native_ffi_when_available(monkeypatch):
    greeter = load_greeter_module(monkeypatch)
    monkeypatch.setattr(greeter.gtk_ffi, "_gtk", None)

    secret = greeter.gtk_ffi.read_password_secret(types.SimpleNamespace(get_text=lambda: "secret"))

    assert secret.as_bytes() == b"secret"
    secret.clear()


def test_read_password_secret_falls_back_to_entry_text(monkeypatch):
    greeter = load_greeter_module(monkeypatch)
    monkeypatch.setattr(greeter.gtk_ffi, "_editable_pointer", lambda entry: None)

    secret = greeter.gtk_ffi.read_password_secret(types.SimpleNamespace(get_text=lambda: "secret"))

    assert secret.as_bytes() == b"secret"
    secret.clear()


def test_handle_event_updates_status_label(monkeypatch):
    greeter = load_greeter_module(monkeypatch)

    class FakeLabel:
        def __init__(self):
            self.text = None

        def set_text(self, text):
            self.text = text

    app = greeter.LoginApp.__new__(greeter.LoginApp)
    app.auth_in_progress = False
    app.username_entry = None
    app.password_entry = None
    app.sessions_entry = None
    app.login_button = None
    app.status_label = FakeLabel()

    greeter.LoginApp.handle_event(
        app,
        {"v": 1, "type": "event", "event": greeter.wldm.protocol.EVENT_SESSION_STARTING, "payload": {}},
    )
    assert app.status_label.text == "Starting session..."

    greeter.LoginApp.handle_event(
        app,
        {"v": 1, "type": "event", "event": greeter.wldm.protocol.EVENT_SESSION_FINISHED, "payload": {"pid": 1, "returncode": 0}},
    )
    assert app.status_label.text == "Session finished."

    greeter.LoginApp.handle_event(
        app,
        {
            "v": 1,
            "type": "event",
            "event": greeter.wldm.protocol.EVENT_SESSION_FINISHED,
            "payload": {"pid": 1, "returncode": 7, "failed": True, "message": "Session failed with exit status 7."},
        },
    )
    assert app.status_label.text == "Session failed with exit status 7."


def test_on_clock_tick_polls_session_finished_event_and_reenables_inputs(monkeypatch):
    greeter = load_greeter_module(monkeypatch)

    class FakeEntry:
        def __init__(self, text=""):
            self.text = text
            self.sensitive = True
            self.focused = False

        def set_text(self, text):
            self.text = text

        def get_text(self):
            return self.text

        def set_sensitive(self, value):
            self.sensitive = value

        def grab_focus(self):
            self.focused = True

    class FakeLabel:
        def __init__(self):
            self.text = None

        def set_text(self, text):
            self.text = text

    class FakeClient:
        def __init__(self):
            self.reads = 0

        def can_read(self):
            return self.reads == 0

        def read_message(self):
            self.reads += 1
            return {
                "v": 1,
                "type": "event",
                "event": greeter.wldm.protocol.EVENT_SESSION_FINISHED,
                "payload": {"pid": 1, "returncode": 0, "failed": False, "message": "Session finished."},
            }

        def close(self):
            return None

    app = greeter.LoginApp.__new__(greeter.LoginApp)
    app.client = FakeClient()
    app.quit = False
    app.auth_in_progress = True
    app.username_entry = FakeEntry("alice")
    app.password_entry = FakeEntry("secret")
    app.sessions_entry = FakeEntry()
    app.login_button = FakeEntry()
    app.status_label = FakeLabel()
    app.date_label = None
    app.time_label = None

    assert greeter.LoginApp.on_clock_tick(app) is True
    assert app.auth_in_progress is False
    assert app.username_entry.text == ""
    assert app.password_entry.text == ""
    assert app.password_entry.focused is True
    assert app.username_entry.sensitive is True
    assert app.password_entry.sensitive is True
    assert app.status_label.text == "Session finished."


def test_poll_events_treats_bad_protocol_as_connection_loss(monkeypatch):
    greeter = load_greeter_module(monkeypatch)
    events = []

    class FakeClient:
        def __init__(self):
            self.reads = 0

        def can_read(self):
            return self.reads == 0

        def read_message(self):
            self.reads += 1
            raise greeter.wldm.protocol.ProtocolError("broken frame", b"\x00\x01bad")

        def close(self):
            return None

    app = greeter.LoginApp.__new__(greeter.LoginApp)
    app.client = FakeClient()
    app.quit = False
    app.status_label = types.SimpleNamespace(set_text=lambda text: events.append(("status", text)))
    monkeypatch.setattr(app, "on_quit", lambda *args: events.append(("quit", True)))
    monkeypatch.setattr(
        greeter.logger,
        "critical",
        lambda msg, *args: events.append(("log", msg % args if args else msg)),
    )

    greeter.LoginApp.poll_events(app)

    assert ("status", "Connection to daemon lost.") in events
    assert ("quit", True) in events
    assert any(item[0] == "log" and "raw=b'\\x00\\x01bad'" in item[1] for item in events)


def test_send_recv_answer_returns_empty_dict_on_bad_protocol(monkeypatch):
    greeter = load_greeter_module(monkeypatch)

    monkeypatch.setattr(greeter.wldm.sessions, "desktop_sessions", lambda username="": [])

    class FakeClient:
        def write_message(self, message):
            return None

        def read_message(self):
            raise greeter.wldm.protocol.ProtocolError("broken frame", b"\x00\x01bad")

        def can_read(self):
            return False

        def close(self):
            return None

    app = greeter.LoginApp(client=FakeClient())

    request = greeter.wldm.protocol.new_request(greeter.wldm.protocol.ACTION_REBOOT, {})

    assert app.send_recv_answer(request) == {}
    assert request["action"] == greeter.wldm.protocol.ACTION_REBOOT


def test_send_recv_answer_treats_bad_protocol_as_connection_loss(monkeypatch):
    greeter = load_greeter_module(monkeypatch)

    monkeypatch.setattr(greeter.wldm.sessions, "desktop_sessions", lambda username="": [])
    events = []

    class FakeClient:
        def write_message(self, message):
            return None

        def read_message(self):
            raise greeter.wldm.protocol.ProtocolError("broken frame", b"\x00\x01bad")

        def can_read(self):
            return False

        def close(self):
            return None

    app = greeter.LoginApp(client=FakeClient())
    app.status_label = types.SimpleNamespace(set_text=lambda text: events.append(("status", text)))
    monkeypatch.setattr(app, "on_quit", lambda *args: events.append(("quit", True)))
    monkeypatch.setattr(
        greeter.logger,
        "critical",
        lambda msg, *args: events.append(("log", msg % args if args else msg)),
    )

    request = greeter.wldm.protocol.new_request(greeter.wldm.protocol.ACTION_REBOOT, {})

    assert app.send_recv_answer(request) == {}
    assert ("status", "Connection to daemon lost.") in events
    assert ("quit", True) in events
    assert any(item[0] == "log" and "raw=b'\\x00\\x01bad'" in item[1] for item in events)


def test_new_ipc_client_requires_socket_env(monkeypatch):
    greeter = load_greeter_module(monkeypatch)

    monkeypatch.delenv("WLDM_SOCKET", raising=False)

    try:
        greeter.new_ipc_client()
    except RuntimeError as exc:
        assert "WLDM_SOCKET" in str(exc)
    else:
        raise AssertionError("new_ipc_client() should have failed")


def test_on_login_clicked_sets_failure_and_clears_password(monkeypatch):
    greeter = load_greeter_module(monkeypatch)

    monkeypatch.setattr(greeter.wldm.sessions, "desktop_sessions", lambda username="": [])

    class FakeEntry:
        def __init__(self, text=""):
            self.text = text
            self.focused = False

        def get_text(self):
            return self.text

        def set_text(self, text):
            self.text = text

        def grab_focus(self):
            self.focused = True

    class FakeLabel:
        def __init__(self):
            self.text = None

        def set_text(self, text):
            self.text = text

    app = greeter.LoginApp(client=DummyClient())
    app.username_entry = FakeEntry("alice")
    app.password_entry = FakeEntry("secret")
    app.status_label = FakeLabel()
    app.sessions = [{"name": "Default", "command": "start-session", "comment": "Default session", "desktop_names": ["default"]}]
    app.sessions_entry = types.SimpleNamespace(
        get_selected_item=lambda: types.SimpleNamespace(get_string=lambda: "Default")
    )
    monkeypatch.setattr(app, "send_recv_answer", lambda data: {"ok": False})

    app.on_login_clicked()

    assert app.status_label.text == "Authentication failed."
    assert app.username_entry.text == "alice"
    assert app.password_entry.text == ""
    assert app.password_entry.focused is True


def test_on_login_clicked_includes_desktop_names_in_auth_request(monkeypatch):
    greeter = load_greeter_module(monkeypatch)
    monkeypatch.setattr(greeter.wldm.sessions, "desktop_sessions", lambda username="": [])

    class FakeEntry:
        def __init__(self, text=""):
            self.text = text

        def get_text(self):
            return self.text

        def set_text(self, text):
            self.text = text

        def grab_focus(self):
            return None

    app = greeter.LoginApp(client=DummyClient())
    app.username_entry = FakeEntry("alice")
    app.password_entry = FakeEntry("secret")
    app.status_label = types.SimpleNamespace(set_text=lambda text: None)
    app.sessions = [{"name": "Sway", "command": "sway", "comment": "Sway", "desktop_names": ["sway", "wlroots"]}]
    app.sessions_entry = types.SimpleNamespace(
        get_selected_item=lambda: types.SimpleNamespace(get_string=lambda: "Sway")
    )
    sent = {}
    monkeypatch.setattr(
        app,
        "send_recv_answer",
        lambda data: sent.update(data) or {"ok": False},
    )

    app.on_login_clicked()

    assert sent["payload"]["desktop_names"] == ["sway", "wlroots"]


def test_on_login_clicked_sets_success_message_and_clears_username(monkeypatch):
    greeter = load_greeter_module(monkeypatch)

    monkeypatch.setattr(greeter.wldm.sessions, "desktop_sessions", lambda username="": [])

    class FakeEntry:
        def __init__(self, text=""):
            self.text = text

        def get_text(self):
            return self.text

        def set_text(self, text):
            self.text = text

    class FakeLabel:
        def __init__(self):
            self.text = None

        def set_text(self, text):
            self.text = text

    app = greeter.LoginApp(client=DummyClient())
    app.username_entry = FakeEntry("alice")
    app.password_entry = FakeEntry("secret")
    app.status_label = FakeLabel()
    app.sessions = []
    app.sessions_entry = None
    monkeypatch.setattr(app, "send_recv_answer",
                        lambda data: {"ok": True, "payload": {"verified": True}})

    app.on_login_clicked()

    assert app.status_label.text == "Authentication accepted. Waiting for session..."
    assert app.username_entry.text == ""
    assert app.password_entry.text == ""


def test_cmd_main_validates_resources_path(monkeypatch, tmp_path):
    greeter = load_greeter_module(monkeypatch)

    monkeypatch.delenv("WLDM_RESOURCES_PATH", raising=False)
    monkeypatch.setattr(greeter.sys, "prefix", str(tmp_path / "missing"))
    assert greeter.cmd_main(types.SimpleNamespace()) == greeter.wldm.EX_FAILURE

    resource_dir = tmp_path / "resources"
    resource_dir.mkdir()
    monkeypatch.setenv("WLDM_RESOURCES_PATH", str(resource_dir))
    monkeypatch.setenv("WLDM_SOCKET", "/tmp/wldm/greeter.sock")
    monkeypatch.setattr(greeter.os.path, "isfile", lambda path: False)

    run_calls = []

    class FakeLoginApp:
        def __init__(self):
            run_calls.append(("init",))

        def run(self):
            run_calls.append(("run",))

    monkeypatch.setattr(greeter, "LoginApp", FakeLoginApp)

    assert greeter.cmd_main(types.SimpleNamespace()) == greeter.wldm.EX_SUCCESS
    assert run_calls == [("init",), ("run",)]


def test_default_resource_path_uses_installed_share_when_env_is_missing(monkeypatch, tmp_path):
    greeter = load_greeter_module(monkeypatch)

    monkeypatch.delenv("WLDM_RESOURCES_PATH", raising=False)
    monkeypatch.setattr(greeter.sys, "prefix", str(tmp_path))

    assert greeter.default_resource_path() == str(tmp_path / "share" / "wldm" / "resources")


def test_themed_resource_path_uses_default_theme(monkeypatch, tmp_path):
    greeter = load_greeter_module(monkeypatch)

    monkeypatch.setenv("WLDM_RESOURCES_PATH", str(tmp_path / "resources"))
    monkeypatch.setenv("WLDM_THEME", "default")

    assert greeter.themed_resource_path() == str(tmp_path / "resources")


def test_themed_resource_path_uses_named_theme_when_present(monkeypatch, tmp_path):
    greeter = load_greeter_module(monkeypatch)
    base = tmp_path / "resources"
    theme_dir = tmp_path / "themes" / "retro"
    base.mkdir()
    theme_dir.mkdir(parents=True)

    monkeypatch.setenv("WLDM_RESOURCES_PATH", str(base))
    monkeypatch.setenv("WLDM_THEME", "retro")

    assert greeter.themed_resource_path() == str(theme_dir)


def test_themed_resource_path_falls_back_to_default_when_theme_is_missing(monkeypatch, tmp_path):
    greeter = load_greeter_module(monkeypatch)
    base = tmp_path / "resources"
    base.mkdir()
    warnings = []

    monkeypatch.setenv("WLDM_RESOURCES_PATH", str(base))
    monkeypatch.setenv("WLDM_THEME", "missing")
    monkeypatch.setattr(greeter.logger, "warning", lambda msg, *args: warnings.append(msg % args if args else msg))

    assert greeter.themed_resource_path() == str(base)
    assert any("falling back to default" in message for message in warnings)


def test_greeter_locale_path_prefers_theme_locale(monkeypatch, tmp_path):
    greeter = load_greeter_module(monkeypatch)
    theme_dir = tmp_path / "themes" / "retro"
    locale_dir = theme_dir / "locale"
    locale_dir.mkdir(parents=True)
    greeter.resource_path = str(theme_dir)
    monkeypatch.delenv("WLDM_LOCALE_PATH", raising=False)

    assert greeter.greeter_locale_path() == str(locale_dir)


def test_greeter_locale_path_prefers_explicit_env(monkeypatch, tmp_path):
    greeter = load_greeter_module(monkeypatch)
    greeter.resource_path = str(tmp_path / "resources")
    monkeypatch.setenv("WLDM_LOCALE_PATH", str(tmp_path / "locale"))

    assert greeter.greeter_locale_path() == str(tmp_path / "locale")


def test_setup_greeter_logging_installs_file_logger_and_excepthook(monkeypatch):
    greeter = load_greeter_module(monkeypatch)

    greeter.setup_greeter_logging()

    assert greeter.sys.excepthook is not greeter.sys.__excepthook__


def test_setup_greeter_i18n_binds_theme_locale(monkeypatch, tmp_path):
    greeter = load_greeter_module(monkeypatch)
    theme_dir = tmp_path / "themes" / "retro"
    locale_dir = theme_dir / "locale"
    locale_dir.mkdir(parents=True)
    greeter.resource_path = str(theme_dir)
    bind_calls = []
    textdomain_calls = []

    monkeypatch.setattr(greeter.locale, "setlocale", lambda category, value: None)
    monkeypatch.setattr(greeter.gettext, "bindtextdomain",
                        lambda domain, path: bind_calls.append((domain, path)))
    monkeypatch.setattr(greeter.gettext, "textdomain",
                        lambda domain: textdomain_calls.append(domain))

    greeter.setup_greeter_i18n()

    assert bind_calls == [("wldm", str(locale_dir))]
    assert textdomain_calls == ["wldm"]


def test_collect_theme_widgets_rejects_missing_required_widgets(monkeypatch):
    greeter = load_greeter_module(monkeypatch)
    monkeypatch.setenv("WLDM_THEME", "retro")
    app = greeter.LoginApp(client=DummyClient())

    class FakeBuilder:
        def get_object(self, name):
            return None if name == "password_entry" else object()

    try:
        app.collect_theme_widgets(FakeBuilder())
    except RuntimeError as exc:
        assert "retro" in str(exc)
        assert "password_entry" in str(exc)
    else:
        raise AssertionError("collect_theme_widgets() should have failed")


def test_collect_theme_widgets_rejects_invalid_required_widget_type(monkeypatch):
    greeter = load_greeter_module(monkeypatch)
    monkeypatch.setenv("WLDM_THEME", "retro")
    app = greeter.LoginApp(client=DummyClient())

    class FakeWindow:
        def set_application(self, app):
            return None

        def present(self):
            return None

    class FakeEntry:
        def get_text(self):
            return ""

        def set_text(self, text):
            return None

        def connect(self, signal, callback):
            return None

        def grab_focus(self):
            return None

    class FakeBuilder:
        def get_object(self, name):
            if name == "main_window":
                return FakeWindow()
            if name == "username_entry":
                return FakeEntry()
            if name == "password_entry":
                return object()
            if name == "login_button":
                return types.SimpleNamespace(connect=lambda *args: None, set_sensitive=lambda value: None)
            return None

    try:
        app.collect_theme_widgets(FakeBuilder())
    except RuntimeError as exc:
        assert "retro" in str(exc)
        assert "password_entry" in str(exc)
    else:
        raise AssertionError("collect_theme_widgets() should reject invalid required widgets")


def test_on_activate_binds_widgets_and_populates_sessions(monkeypatch):
    greeter = load_greeter_module(monkeypatch)
    greeter.resource_path = "/tmp/resources"

    class FakeWindow:
        def __init__(self):
            self.application = None
            self.presented = False

        def set_application(self, app):
            self.application = app

        def present(self):
            self.presented = True

    class FakeEntry:
        def __init__(self):
            self.connections = []
            self.text = ""
            self.focused = False

        def connect(self, signal, callback):
            self.connections.append((signal, callback))

        def get_text(self):
            return self.text

        def set_text(self, text):
            self.text = text

        def grab_focus(self):
            self.focused = True

    class FakeButton:
        def __init__(self):
            self.connections = []
            self.visible = None
            self.sensitive = True

        def connect(self, signal, callback):
            self.connections.append((signal, callback))

        def set_visible(self, visible):
            self.visible = visible

        def set_sensitive(self, value):
            self.sensitive = value

    class FakeSessionsEntry(FakeEntry):
        def __init__(self):
            super().__init__()
            self.model = None
            self.selected = None

        def set_model(self, model):
            self.model = model

        def set_selected(self, idx):
            self.selected = idx

        def get_selected_item(self):
            if self.model is None or self.selected is None:
                return None
            return types.SimpleNamespace(get_string=lambda: self.model.items[self.selected])

    window = FakeWindow()
    username_entry = FakeEntry()
    password_entry = FakeEntry()
    status_label = types.SimpleNamespace(set_text=lambda text: None)
    sessions_entry = FakeSessionsEntry()
    login_button = FakeButton()
    quit_button = FakeButton()
    reboot_button = FakeButton()
    suspend_button = FakeButton()
    hibernate_button = FakeButton()
    hostname_label = types.SimpleNamespace(set_text=lambda text: None)
    date_label = types.SimpleNamespace(set_text=lambda text: None)
    time_label = types.SimpleNamespace(set_text=lambda text: None)
    session_label = types.SimpleNamespace(set_text=lambda text: None)
    identity_label = types.SimpleNamespace(set_text=lambda text: None)
    avatar_label = types.SimpleNamespace(set_text=lambda text: None)
    objects = {
        "main_window": window,
        "username_entry": username_entry,
        "password_entry": password_entry,
        "sessions_entry": sessions_entry,
        "status_label": status_label,
        "login_button": login_button,
        "quit_button": quit_button,
        "reboot_button": reboot_button,
        "suspend_button": suspend_button,
        "hibernate_button": hibernate_button,
        "hostname_label": hostname_label,
        "date_label": date_label,
        "time_label": time_label,
        "session_label": session_label,
        "identity_label": identity_label,
        "avatar_label": avatar_label,
    }

    class FakeBuilder:
        def __init__(self):
            self.translation_domain = None
            self.loaded_path = None

        def set_translation_domain(self, domain):
            self.translation_domain = domain

        def add_from_file(self, path):
            self.loaded_path = path

        def get_object(self, name):
            return objects[name]

    monkeypatch.setattr(greeter.wldm.sessions, "desktop_sessions",
                        lambda username="": [
                            {"name": "Alpha", "command": "alpha", "comment": "Alpha session", "desktop_names": ["alpha"]},
                            {"name": "Beta", "command": "beta", "comment": "Beta session", "desktop_names": ["beta"]},
                        ])
    monkeypatch.setenv("WLDM_ACTIONS", "poweroff:reboot")
    monkeypatch.setattr(greeter.Gtk.Builder, "new", lambda: FakeBuilder())

    app = greeter.LoginApp(client=DummyClient())
    app.on_activate(app.app)

    assert window.application is app.app
    assert window.presented is True
    assert sessions_entry.model.items == ["Alpha", "Beta"]
    assert sessions_entry.selected == 0
    assert login_button.connections == [("clicked", app.on_login_clicked)]
    assert quit_button.connections == [("clicked", app.on_poweroff_clicked)]
    assert reboot_button.connections == [("clicked", app.on_reboot_clicked)]
    assert suspend_button.connections == [("clicked", app.on_suspend_clicked)]
    assert hibernate_button.connections == [("clicked", app.on_hibernate_clicked)]
    assert quit_button.visible is True
    assert reboot_button.visible is True
    assert suspend_button.visible is False
    assert hibernate_button.visible is False
    assert password_entry.connections == [("activate", app.on_login_clicked)]
    assert sessions_entry.connections == [
        ("notify::selected-item", app.on_session_changed),
        ("activate", app.on_login_clicked),
    ]
    assert username_entry.connections == [("changed", app.on_username_changed)]
    assert greeter._test_timeout_calls == [(1, app.on_clock_tick)]  # type: ignore[attr-defined]


def test_cmd_main_loads_css_when_present(monkeypatch, tmp_path):
    greeter = load_greeter_module(monkeypatch)
    resource_dir = tmp_path / "resources"
    resource_dir.mkdir()
    css_path = resource_dir / "style.css"
    css_path.write_text("label {}", encoding="utf-8")

    monkeypatch.setenv("WLDM_RESOURCES_PATH", str(resource_dir))
    monkeypatch.setenv("WLDM_SOCKET", "/tmp/wldm/greeter.sock")
    monkeypatch.setattr(greeter.os.path, "isdir", lambda path: True)
    monkeypatch.setattr(greeter.os.path, "isfile", lambda path: path == str(css_path))

    css_loaded = []
    provider_calls = []

    class FakeCssProvider:
        def load_from_path(self, path):
            css_loaded.append(path)

    monkeypatch.setattr(greeter.Gtk, "CssProvider", FakeCssProvider)
    monkeypatch.setattr(
        greeter.Gtk.StyleContext,
        "add_provider_for_display",
        lambda display, provider, priority: provider_calls.append((display, provider, priority)),
    )

    run_calls = []

    class FakeLoginApp:
        def __init__(self):
            run_calls.append(("init",))

        def run(self):
            run_calls.append(("run",))

    monkeypatch.setattr(greeter, "LoginApp", FakeLoginApp)

    assert greeter.cmd_main(types.SimpleNamespace()) == greeter.wldm.EX_SUCCESS
    assert css_loaded == [str(css_path)]
    assert len(provider_calls) == 1
    assert run_calls == [("init",), ("run",)]


def test_system_action_buttons_send_requests(monkeypatch):
    greeter = load_greeter_module(monkeypatch)
    monkeypatch.setattr(greeter.wldm.sessions, "desktop_sessions", lambda username="": [])

    class FakeLabel:
        def __init__(self):
            self.text = None

        def set_text(self, text):
            self.text = text

    app = greeter.LoginApp(client=DummyClient())
    app.status_label = FakeLabel()
    calls = []

    monkeypatch.setattr(
        app,
        "send_recv_answer",
        lambda data: calls.append(data["action"]) or {"ok": True, "payload": {"accepted": True}},
    )

    app.on_reboot_clicked()
    assert app.status_label.text == "Rebooting..."

    app.on_poweroff_clicked()
    assert app.status_label.text == "Powering off..."
    app.on_suspend_clicked()
    assert app.status_label.text == "Suspending..."

    app.on_hibernate_clicked()
    assert app.status_label.text == "Hibernating..."
    assert calls == [
        greeter.wldm.protocol.ACTION_REBOOT,
        greeter.wldm.protocol.ACTION_POWEROFF,
        greeter.wldm.protocol.ACTION_SUSPEND,
        greeter.wldm.protocol.ACTION_HIBERNATE,
    ]


def test_username_change_updates_identity_preview(monkeypatch):
    greeter = load_greeter_module(monkeypatch)
    calls = []
    monkeypatch.setattr(greeter, "account_service_profile",
                        lambda username: {"display_name": "Alice Doe", "avatar_path": ""})
    monkeypatch.setattr(greeter.wldm.sessions, "desktop_sessions",
                        lambda username="": calls.append(username) or [
                            {"name": "Sway", "command": "sway --debug", "comment": "User sway", "desktop_names": ["sway", "wlroots"]},
                        ])

    class FakeEntry:
        def get_text(self):
            return "alice"

    class FakeLabel:
        def __init__(self):
            self.text = None

        def set_text(self, text):
            self.text = text

    app = greeter.LoginApp.__new__(greeter.LoginApp)
    app.username_entry = FakeEntry()
    app.identity_label = FakeLabel()
    app.avatar_label = FakeLabel()
    app.sessions = []
    app.session_label = None
    app.sessions_entry = None

    greeter.LoginApp.on_username_changed(app)

    assert app.identity_label.text == "Alice Doe"
    assert app.avatar_label.text == "A"
    assert calls == ["alice"]
