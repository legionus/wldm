# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026  Alexey Gladkov <legion@kernel.org>

import importlib
import json
import pwd
import sys
import types


def load_greeter_module(monkeypatch):
    timeout_calls = []

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
        Builder=types.SimpleNamespace(new_from_file=lambda path: None),
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
    def writeline(self, data):
        return None

    def readline(self):
        return ""

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
                "a.desktop": {"type": "Application", "name": "Alpha", "exec": "alpha", "comment": "Alpha session"},
                "b.desktop": {"type": "Application", "name": "Beta", "exec": "beta", "comment": "Beta session"},
            }
            return data.get(base, {}).get(option, fallback)

    monkeypatch.setattr(greeter.os, "scandir", lambda path: FakeScandir())
    monkeypatch.setattr(greeter.configparser, "ConfigParser", FakeConfig)

    assert greeter.desktop_sessions() == [["Alpha", "alpha", "Alpha session"], ["Beta", "beta", "Beta session"]]


def test_session_data_dirs_prepends_user_directory(monkeypatch):
    greeter = load_greeter_module(monkeypatch)
    pw = pwd.struct_passwd(("alice", "x", 1000, 1000, "", "/home/alice", "/bin/sh"))

    monkeypatch.setenv("WLDM_GREETER_USER_SESSIONS", "yes")
    monkeypatch.setattr(greeter.pwd, "getpwnam", lambda username: pw)

    assert greeter.session_data_dirs("alice") == [
        "/home/alice/.local/share/wayland-sessions",
        "/usr/share/wayland-sessions",
    ]


def test_session_data_dirs_can_disable_user_sessions(monkeypatch):
    greeter = load_greeter_module(monkeypatch)

    monkeypatch.setenv("WLDM_GREETER_USER_SESSIONS", "no")

    assert greeter.session_data_dirs("alice") == ["/usr/share/wayland-sessions"]


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

    monkeypatch.setattr(greeter, "session_data_dirs",
                        lambda username="": ["/home/alice/.local/share/wayland-sessions", "/usr/share/wayland-sessions"])

    def fake_scandir(path):
        scanned_paths.append(path)
        if path.startswith("/home/alice"):
            return FakeScandir([FakeEntry("user.desktop")])
        return FakeScandir([FakeEntry("system.desktop"), FakeEntry("labwc.desktop")])

    monkeypatch.setattr(greeter.os, "scandir", fake_scandir)
    monkeypatch.setattr(greeter.configparser, "ConfigParser", FakeConfig)

    assert greeter.desktop_sessions("alice") == [
        ["Labwc", "labwc", "Labwc"],
        ["Sway", "sway --debug", "User sway"],
    ]
    assert scanned_paths == [
        "/home/alice/.local/share/wayland-sessions",
        "/usr/share/wayland-sessions",
    ]


def test_desktop_sessions_handles_oserror(monkeypatch):
    greeter = load_greeter_module(monkeypatch)

    monkeypatch.setattr(
        greeter.os,
        "scandir",
        lambda path: (_ for _ in ()).throw(OSError("boom")),
    )

    assert greeter.desktop_sessions() == []


def test_get_session_command_returns_selected_command(monkeypatch):
    greeter = load_greeter_module(monkeypatch)

    class FakeItem:
        def get_string(self):
            return "Beta"

    app = greeter.LoginApp.__new__(greeter.LoginApp)
    app.sessions = [["Alpha", "alpha", "Alpha session"], ["Beta", "beta --flag", "Beta session"]]
    app.sessions_entry = types.SimpleNamespace(get_selected_item=lambda: FakeItem())

    assert greeter.LoginApp.get_session_command(app) == "beta --flag"


def test_get_session_command_handles_missing_selection(monkeypatch):
    greeter = load_greeter_module(monkeypatch)

    app = greeter.LoginApp.__new__(greeter.LoginApp)
    app.sessions = [["Alpha", "alpha", "Alpha session"]]
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

    monkeypatch.setattr(greeter, "desktop_sessions", lambda username="": [])

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


def test_send_recv_answer_round_trips_json(monkeypatch):
    greeter = load_greeter_module(monkeypatch)

    monkeypatch.setattr(greeter, "desktop_sessions", lambda username="": [])

    class FakeClient:
        def __init__(self, lines):
            self.lines = iter(lines)
            self.sent = []
            self.readable = False

        def writeline(self, data):
            self.sent.append(data)

        def readline(self):
            return next(self.lines, "")

        def can_read(self):
            return self.readable

        def close(self):
            return None

    request = greeter.wldm.protocol.new_request(greeter.wldm.protocol.ACTION_REBOOT, {})
    client = FakeClient([
        f'{{"v": 1, "type": "event", "event": "{greeter.wldm.protocol.EVENT_SESSION_FINISHED}", "payload": {{"pid": 1, "returncode": 0}}}}\n',
        f'{{"v": 1, "id": "{request["id"]}", "type": "response", "action": "{greeter.wldm.protocol.ACTION_REBOOT}", "ok": true, "payload": {{"accepted": true}}}}\n',
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
    sent = json.loads(client.sent[0])
    assert sent["v"] == 1
    assert sent["type"] == "request"
    assert sent["action"] == greeter.wldm.protocol.ACTION_REBOOT
    assert sent["payload"] == {}


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
        {"v": 1, "type": "event", "event": greeter.wldm.protocol.EVENT_SESSION_STARTING, "payload": {"username": "alice"}},
    )
    assert app.status_label.text == "Starting session for alice..."

    greeter.LoginApp.handle_event(
        app,
        {"v": 1, "type": "event", "event": greeter.wldm.protocol.EVENT_SESSION_FINISHED, "payload": {"pid": 1, "returncode": 0}},
    )
    assert app.status_label.text == "Session finished."


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

        def readline(self):
            self.reads += 1
            return (
                f'{{"v": 1, "type": "event", "event": "{greeter.wldm.protocol.EVENT_SESSION_FINISHED}", '
                f'"payload": {{"pid": 1, "returncode": 0}}}}\n'
            )

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


def test_send_recv_answer_returns_empty_dict_on_bad_json(monkeypatch):
    greeter = load_greeter_module(monkeypatch)

    monkeypatch.setattr(greeter, "desktop_sessions", lambda username="": [])

    class FakeClient:
        def writeline(self, data):
            return None

        def readline(self):
            return "{bad json}\n"

        def can_read(self):
            return False

        def close(self):
            return None

    app = greeter.LoginApp(client=FakeClient())

    request = greeter.wldm.protocol.new_request(greeter.wldm.protocol.ACTION_REBOOT, {})

    assert app.send_recv_answer(request) == {}
    assert request["action"] == greeter.wldm.protocol.ACTION_REBOOT


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

    monkeypatch.setattr(greeter, "desktop_sessions", lambda username="": [])

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
    app.sessions = [["Default", "start-session", "Default session"]]
    app.sessions_entry = types.SimpleNamespace(
        get_selected_item=lambda: types.SimpleNamespace(get_string=lambda: "Default")
    )
    monkeypatch.setattr(app, "send_recv_answer", lambda data: {"ok": False})

    app.on_login_clicked()

    assert app.status_label.text == "Authentication failed."
    assert app.username_entry.text == "alice"
    assert app.password_entry.text == ""
    assert app.password_entry.focused is True


def test_on_login_clicked_sets_success_message_and_clears_username(monkeypatch):
    greeter = load_greeter_module(monkeypatch)

    monkeypatch.setattr(greeter, "desktop_sessions", lambda username="": [])

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


def test_setup_greeter_logging_installs_file_logger_and_excepthook(monkeypatch):
    greeter = load_greeter_module(monkeypatch)

    greeter.setup_greeter_logging()

    assert greeter.sys.excepthook is not greeter.sys.__excepthook__


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

        def connect(self, signal, callback):
            self.connections.append((signal, callback))

        def get_text(self):
            return self.text

    class FakeButton:
        def __init__(self):
            self.connections = []

        def connect(self, signal, callback):
            self.connections.append((signal, callback))

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
        "hostname_label": hostname_label,
        "date_label": date_label,
        "time_label": time_label,
        "session_label": session_label,
        "identity_label": identity_label,
        "avatar_label": avatar_label,
    }

    class FakeBuilder:
        def get_object(self, name):
            return objects[name]

    monkeypatch.setattr(greeter, "desktop_sessions",
                        lambda username="": [["Alpha", "alpha", "Alpha session"], ["Beta", "beta", "Beta session"]])
    monkeypatch.setattr(greeter.Gtk.Builder, "new_from_file", lambda path: FakeBuilder())

    app = greeter.LoginApp(client=DummyClient())
    app.on_activate(app.app)

    assert window.application is app.app
    assert window.presented is True
    assert sessions_entry.model.items == ["Alpha", "Beta"]
    assert sessions_entry.selected == 0
    assert login_button.connections == [("clicked", app.on_login_clicked)]
    assert quit_button.connections == [("clicked", app.on_poweroff_clicked)]
    assert reboot_button.connections == [("clicked", app.on_reboot_clicked)]
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
    monkeypatch.setattr(greeter, "desktop_sessions", lambda username="": [])

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
    assert calls == [greeter.wldm.protocol.ACTION_REBOOT, greeter.wldm.protocol.ACTION_POWEROFF]


def test_username_change_updates_identity_preview(monkeypatch):
    greeter = load_greeter_module(monkeypatch)
    calls = []
    monkeypatch.setattr(greeter, "account_service_profile",
                        lambda username: {"display_name": "Alice Doe", "avatar_path": ""})
    monkeypatch.setattr(greeter, "desktop_sessions",
                        lambda username="": calls.append(username) or [["Sway", "sway --debug", "User sway"]])

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
