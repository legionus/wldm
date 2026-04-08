# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026  Alexey Gladkov <legion@kernel.org>

import importlib
import pwd
import types

from tests.helpers_greeter import (
    DummyButton,
    DummyClient,
    DummyLabel,
    StubBox,
    StubBuilder,
    StubEntry,
    StubSessionsEntry,
    StubStatusLabel,
    StubWindow,
    load_greeter_module,
    make_activate_objects,
    new_greeter_app,
    selected_entry,
)


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

    monkeypatch.setattr(greeter.wldm.sessions.os, "scandir", lambda path: FakeScandir())
    monkeypatch.setattr(
        greeter.wldm.inifile,
        "read_ini_file",
        lambda path, **kwargs: greeter.wldm.inifile.IniFile({
            "Desktop Entry": {
                "Type": "Application",
                "Name": "Alpha" if path.endswith("a.desktop") else "Beta",
                "Exec": "alpha" if path.endswith("a.desktop") else "beta",
                "Comment": "Alpha session" if path.endswith("a.desktop") else "Beta session",
                "DesktopNames": "AlphaDesktop;WL;" if path.endswith("a.desktop") else "",
            },
        }),
    )

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

    assert greeter.wldm.sessions._session_data_dirs("alice") == [
        "/home/alice/.config/wldm/sessions",
        "/usr/share/wayland-sessions",
        "/opt/wayland-sessions",
    ]


def test_session_data_dirs_can_disable_user_sessions(monkeypatch):
    greeter = load_greeter_module(monkeypatch)

    monkeypatch.setenv("WLDM_GREETER_USER_SESSIONS", "no")
    monkeypatch.setenv("WLDM_GREETER_SESSION_DIRS", "/usr/share/wayland-sessions:/opt/wayland-sessions")

    assert greeter.wldm.sessions._session_data_dirs("alice") == [
        "/usr/share/wayland-sessions",
        "/opt/wayland-sessions",
    ]


def test_available_actions_reads_environment(monkeypatch):
    greeter = load_greeter_module(monkeypatch)

    monkeypatch.setenv("WLDM_ACTIONS", "poweroff:reboot:suspend")

    assert greeter._available_actions() == {"poweroff", "reboot", "suspend"}


def test_keyboard_state_reads_active_layout(monkeypatch):
    greeter = load_greeter_module(monkeypatch)
    greeter_keyboard = importlib.import_module("wldm.greeter_keyboard")

    class FakeKeyboard:
        def get_layout_names(self):
            return ["English (US)", "Russian"]

        def get_active_layout_index(self):
            return 1

    class FakeSeat:
        def get_keyboard(self):
            return FakeKeyboard()

    class FakeDisplay:
        def get_default_seat(self):
            return FakeSeat()

    monkeypatch.setattr(greeter_keyboard.Gdk.Display, "get_default", lambda: FakeDisplay())
    monkeypatch.setenv("XKB_DEFAULT_LAYOUT", "us,ru")

    layouts, active_index = greeter_keyboard.keyboard_state()

    assert active_index == 1
    assert layouts == [
        greeter_keyboard.KeyboardLayout(short_name="us", long_name="English (US)"),
        greeter_keyboard.KeyboardLayout(short_name="ru", long_name="Russian"),
    ]


def test_keyboard_state_returns_empty_without_gtk418_api(monkeypatch):
    greeter = load_greeter_module(monkeypatch)
    greeter_keyboard = importlib.import_module("wldm.greeter_keyboard")

    class FakeKeyboard:
        pass

    class FakeSeat:
        def get_keyboard(self):
            return FakeKeyboard()

    class FakeDisplay:
        def get_default_seat(self):
            return FakeSeat()

    monkeypatch.setattr(greeter_keyboard.Gdk.Display, "get_default", lambda: FakeDisplay())

    assert greeter_keyboard.keyboard_state() == ([], -1)


def test_update_keyboard_indicator_sets_visibility_from_active_layout(monkeypatch):
    greeter = load_greeter_module(monkeypatch)
    greeter_keyboard = importlib.import_module("wldm.greeter_keyboard")
    app = new_greeter_app(greeter, keyboard_label=DummyLabel())

    monkeypatch.setattr(
        greeter_keyboard,
        "keyboard_state",
        lambda: (
            [
                greeter_keyboard.KeyboardLayout(short_name="us", long_name="English (US)"),
                greeter_keyboard.KeyboardLayout(short_name="ru", long_name="Russian"),
            ],
            1,
        ),
    )

    greeter.GreeterApp.update_keyboard_indicator(app)

    assert app.keyboard_label.text == "RU"
    assert app.keyboard_label.tooltip == "Russian"
    assert app.keyboard_label.width_chars == 2
    assert app.keyboard_label.visible is True


def test_refresh_sessions_prefers_last_session_command(monkeypatch):
    greeter = load_greeter_module(monkeypatch)
    app = new_greeter_app(greeter, last_session_command="labwc", sessions_entry=StubSessionsEntry())

    monkeypatch.setattr(
        greeter.wldm.sessions,
        "desktop_sessions",
        lambda username="": [
            {"name": "Sway", "command": "sway", "comment": "Sway", "desktop_names": ["sway"]},
            {"name": "Labwc", "command": "labwc", "comment": "Labwc", "desktop_names": ["labwc"]},
        ],
    )

    greeter.GreeterApp.refresh_sessions(app)

    assert app.sessions_entry.selected == 1


def test_refresh_sessions_explicit_preference_overrides_previous_selection(monkeypatch):
    greeter = load_greeter_module(monkeypatch)
    app = new_greeter_app(greeter, sessions=[
        {"name": "Sway", "command": "sway", "comment": "Sway", "desktop_names": ["sway"]},
        {"name": "Labwc", "command": "labwc", "comment": "Labwc", "desktop_names": ["labwc"]},
    ])

    class FakeItem:
        def __init__(self, text):
            self.text = text

        def get_string(self):
            return self.text

    app.sessions_entry = StubSessionsEntry(selected_item=FakeItem("Sway"))

    monkeypatch.setattr(
        greeter.wldm.sessions,
        "desktop_sessions",
        lambda username="": [
            {"name": "Sway", "command": "sway", "comment": "Sway", "desktop_names": ["sway"]},
            {"name": "Labwc", "command": "labwc", "comment": "Labwc", "desktop_names": ["labwc"]},
        ],
    )

    app.sessions_entry.set_model(greeter.Gtk.StringList())
    app.sessions_entry.model.append("Sway")
    app.sessions_entry.model.append("Labwc")

    greeter.GreeterApp.refresh_sessions(app, preferred_command="labwc")

    assert app.sessions_entry.selected == 1



def test_login_app_uses_project_application_id(monkeypatch):
    greeter = load_greeter_module(monkeypatch)

    monkeypatch.setattr(greeter.wldm.sessions, "desktop_sessions", lambda username="": [])

    app = greeter.GreeterApp(client=DummyClient())

    assert app.app.application_id == "org.wldm.greeter"


def test_parse_desktop_names_splits_semicolon_list(monkeypatch):
    greeter = load_greeter_module(monkeypatch)

    assert greeter.wldm.sessions._parse_desktop_names("GNOME;GNOME-Classic;") == ["GNOME", "GNOME-Classic"]


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

    monkeypatch.setattr(greeter.wldm.sessions, "_session_data_dirs",
                        lambda username="": ["/home/alice/.local/share/wayland-sessions", "/usr/share/wayland-sessions"])

    def fake_scandir(path):
        scanned_paths.append(path)
        if path.startswith("/home/alice"):
            return FakeScandir([FakeEntry("user.desktop")])
        return FakeScandir([FakeEntry("system.desktop"), FakeEntry("labwc.desktop")])

    monkeypatch.setattr(greeter.wldm.sessions.os, "scandir", fake_scandir)
    monkeypatch.setattr(
        greeter.wldm.inifile,
        "read_ini_file",
        lambda path, **kwargs: greeter.wldm.inifile.IniFile({
            "Desktop Entry": {
                "Type": "Application",
                "Name": "Sway" if path.endswith(("user.desktop", "system.desktop")) else "Labwc",
                "Exec": "sway --debug" if path.endswith("user.desktop") else "sway" if path.endswith("system.desktop") else "labwc",
                "Comment": "User sway" if path.endswith("user.desktop") else "System sway" if path.endswith("system.desktop") else "Labwc",
                "DesktopNames": "",
            },
        }),
    )

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


def test_desktop_sessions_ignores_invalid_entries(monkeypatch):
    greeter = load_greeter_module(monkeypatch)

    class FakeEntry:
        def __init__(self, name):
            self.name = name

        def is_file(self):
            return True

    class FakeScandir:
        def __enter__(self):
            return iter([FakeEntry("broken.desktop"), FakeEntry("good.desktop")])

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(greeter.wldm.sessions.os, "scandir", lambda path: FakeScandir())
    monkeypatch.setattr(
        greeter.wldm.inifile,
        "read_ini_file",
        lambda path, **kwargs: (_ for _ in ()).throw(ValueError("bad entry"))
        if path.endswith("broken.desktop") else
        greeter.wldm.inifile.IniFile(
            {"Desktop Entry": {"Type": "Application", "Name": "Good", "Exec": "good", "Comment": "Good session"}}
        ),
    )

    assert greeter.wldm.sessions.desktop_sessions() == [
        {"name": "Good", "command": "good", "comment": "Good session", "desktop_names": ["good"]},
    ]


def test_get_session_command_returns_selected_command(monkeypatch):
    greeter = load_greeter_module(monkeypatch)

    class FakeItem:
        def get_string(self):
            return "Beta"

    app = new_greeter_app(greeter, sessions=[
        {"name": "Alpha", "command": "alpha", "comment": "Alpha session", "desktop_names": ["alpha"]},
        {"name": "Beta", "command": "beta --flag", "comment": "Beta session", "desktop_names": ["beta"]},
    ], sessions_entry=types.SimpleNamespace(get_selected_item=lambda: FakeItem()))

    assert greeter.GreeterApp.get_session_command(app) == "beta --flag"


def test_get_session_command_handles_missing_selection(monkeypatch):
    greeter = load_greeter_module(monkeypatch)
    app = new_greeter_app(
        greeter,
        sessions=[{"name": "Alpha", "command": "alpha", "comment": "Alpha session", "desktop_names": ["alpha"]}],
        sessions_entry=types.SimpleNamespace(get_selected_item=lambda: None),
    )

    assert greeter.GreeterApp.get_session_command(app) == ""


def test_account_service_profile_reads_real_name(monkeypatch, tmp_path):
    greeter = load_greeter_module(monkeypatch)
    greeter_account = importlib.import_module("wldm.greeter_account")
    profile_dir = tmp_path / "AccountsService" / "users"
    profile_dir.mkdir(parents=True)
    profile_file = profile_dir / "alice"
    profile_file.write_text("[User]\nRealName=Alice Doe\nIcon=/missing/icon.png\n", encoding="utf-8")

    monkeypatch.setattr(greeter_account.os.path, "join", lambda *parts: str(profile_file) if parts[-1] == "alice" else "/".join(parts))

    profile = greeter_account.account_service_profile("alice")

    assert profile is not None
    assert profile["display_name"] == "Alice Doe"
    assert profile["avatar_path"] == ""


def test_account_service_profile_ignores_parse_errors(monkeypatch, tmp_path):
    greeter = load_greeter_module(monkeypatch)
    greeter_account = importlib.import_module("wldm.greeter_account")
    profile_dir = tmp_path / "AccountsService" / "users"
    profile_dir.mkdir(parents=True)
    profile_file = profile_dir / "alice"
    profile_file.write_text("not an ini file\n", encoding="utf-8")

    monkeypatch.setattr(greeter_account.os.path, "join", lambda *parts: str(profile_file) if parts[-1] == "alice" else "/".join(parts))

    profile = greeter_account.account_service_profile("alice")

    assert profile is None


def test_account_service_profile_ignores_oversized_files(monkeypatch, tmp_path):
    greeter = load_greeter_module(monkeypatch)
    greeter_account = importlib.import_module("wldm.greeter_account")
    profile_dir = tmp_path / "AccountsService" / "users"
    profile_dir.mkdir(parents=True)
    profile_file = profile_dir / "alice"
    profile_file.write_text("A" * (greeter.wldm.policy.ACCOUNT_SERVICE_MAX_FILE_SIZE + 1), encoding="utf-8")

    monkeypatch.setattr(greeter_account.os.path, "join", lambda *parts: str(profile_file) if parts[-1] == "alice" else "/".join(parts))

    profile = greeter_account.account_service_profile("alice")

    assert profile is None


def test_login_app_run_calls_application_run(monkeypatch):
    greeter = load_greeter_module(monkeypatch)

    monkeypatch.setattr(greeter.wldm.sessions, "desktop_sessions", lambda username="": [])

    app = greeter.GreeterApp(client=DummyClient())
    app.run()

    assert app.app.run_called is True


def test_update_clock_sets_date_and_time(monkeypatch):
    greeter = load_greeter_module(monkeypatch)
    app = new_greeter_app(greeter, date_label=DummyLabel(), time_label=DummyLabel())
    monkeypatch.setattr(greeter.greeter_ui.time, "strftime",
                        lambda fmt: {"%A, %d %B": "Monday, 30 March", "%H:%M": "09:45"}[fmt])

    greeter.GreeterApp.update_clock(app)

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

    request = greeter.greeter_protocol.new_request(greeter.greeter_protocol.ACTION_REBOOT, {})
    client = FakeClient([
        {
            "v": 1,
            "type": "event",
            "event": greeter.greeter_protocol.EVENT_SESSION_FINISHED,
            "payload": {"pid": 1, "returncode": 0},
        },
        {
            "v": 1,
            "id": request["id"],
            "type": "response",
            "action": greeter.greeter_protocol.ACTION_REBOOT,
            "ok": True,
            "payload": {"accepted": True},
        },
    ])
    app = greeter.GreeterApp(client=client)

    answer = app.send_recv_answer(request)

    assert answer == {
        "v": 1,
        "id": request["id"],
        "type": "response",
        "action": greeter.greeter_protocol.ACTION_REBOOT,
        "ok": True,
        "payload": {"accepted": True},
    }
    sent = client.sent[0]
    assert sent["v"] == 1
    assert sent["type"] == "request"
    assert sent["action"] == greeter.greeter_protocol.ACTION_REBOOT
    assert sent["payload"] == {}


def test_read_password_secret_uses_native_ffi_when_available(monkeypatch):
    greeter = load_greeter_module(monkeypatch)
    monkeypatch.setattr(greeter.greeter_auth.gtk_ffi, "_load_gtk_library", lambda: None)

    secret = greeter.greeter_auth.gtk_ffi.read_password_secret(types.SimpleNamespace(get_text=lambda: "secret"))

    assert secret.as_bytes() == b"secret"
    secret.clear()


def test_read_password_secret_falls_back_to_entry_text(monkeypatch):
    greeter = load_greeter_module(monkeypatch)
    monkeypatch.setattr(greeter.greeter_auth.gtk_ffi, "_editable_pointer", lambda entry: None)

    secret = greeter.greeter_auth.gtk_ffi.read_password_secret(types.SimpleNamespace(get_text=lambda: "secret"))

    assert secret.as_bytes() == b"secret"
    secret.clear()


def test_handle_event_updates_status_label(monkeypatch):
    greeter = load_greeter_module(monkeypatch)
    app = new_greeter_app(greeter, status_label=DummyLabel())

    greeter.GreeterApp.handle_event(
        app,
        {"v": 1, "type": "event", "event": greeter.greeter_protocol.EVENT_SESSION_STARTING, "payload": {}},
    )
    assert app.status_label.text == "Starting session..."

    greeter.GreeterApp.handle_event(
        app,
        {"v": 1, "type": "event", "event": greeter.greeter_protocol.EVENT_SESSION_FINISHED, "payload": {"pid": 1, "returncode": 0}},
    )
    assert app.status_label.text == "Session finished."

    greeter.GreeterApp.handle_event(
        app,
        {
            "v": 1,
            "type": "event",
            "event": greeter.greeter_protocol.EVENT_SESSION_FINISHED,
            "payload": {"pid": 1, "returncode": 7, "failed": True, "message": "Session failed with exit status 7."},
        },
    )
    assert app.status_label.text == "Session failed with exit status 7."


def test_login_app_loads_last_session_from_state_file(monkeypatch, tmp_path):
    greeter = load_greeter_module(monkeypatch)
    state_file = tmp_path / "last-session"

    monkeypatch.setenv("WLDM_STATE_FILE", str(state_file))
    monkeypatch.setattr(greeter.wldm.state, "load_last_session_file", lambda path: ("alice", "labwc"))

    app = greeter.GreeterApp(client=DummyClient())

    assert app.state_file == str(state_file)
    assert app.last_username == "alice"
    assert app.last_session_command == "labwc"


def test_handle_event_saves_last_session_state_on_success(monkeypatch):
    greeter = load_greeter_module(monkeypatch)
    calls = []
    app = new_greeter_app(
        greeter,
        state_file="/tmp/wldm-state/last-session",
        last_session_command="labwc",
        username_entry=StubEntry("alice"),
        password_entry=StubEntry("secret"),
        status_label=DummyLabel(),
    )
    app.refresh_sessions = lambda username="", preferred_command="": None
    app.get_session_command = lambda: "labwc"

    monkeypatch.setattr(
        greeter.wldm.state,
        "save_last_session_file",
        lambda path, username, command: calls.append((path, username, command)),
    )

    greeter.GreeterApp.handle_event(
        app,
        {
            "v": 1,
            "type": "event",
            "event": greeter.greeter_protocol.EVENT_SESSION_FINISHED,
            "payload": {"pid": 1, "returncode": 0, "failed": False, "message": "Session finished."},
        },
    )

    assert calls == [("/tmp/wldm-state/last-session", "alice", "labwc")]


def test_handle_event_saves_last_session_state_when_username_entry_was_cleared(monkeypatch):
    greeter = load_greeter_module(monkeypatch)
    calls = []
    app = new_greeter_app(
        greeter,
        state_file="/tmp/wldm-state/last-session",
        last_username="alice",
        last_session_command="labwc",
        username_entry=StubEntry(""),
        password_entry=StubEntry("secret"),
        status_label=DummyLabel(),
    )
    app.refresh_sessions = lambda username="", preferred_command="": None
    app.get_session_command = lambda: "labwc"

    monkeypatch.setattr(
        greeter.wldm.state,
        "save_last_session_file",
        lambda path, username, command: calls.append((path, username, command)),
    )

    greeter.GreeterApp.handle_event(
        app,
        {
            "v": 1,
            "type": "event",
            "event": greeter.greeter_protocol.EVENT_SESSION_FINISHED,
            "payload": {"pid": 1, "returncode": 0, "failed": False, "message": "Session finished."},
        },
    )

    assert calls == [("/tmp/wldm-state/last-session", "alice", "labwc")]


def test_handle_event_keeps_remembered_session_command_when_current_selection_changed(monkeypatch):
    greeter = load_greeter_module(monkeypatch)
    calls = []
    app = new_greeter_app(
        greeter,
        state_file="/tmp/wldm-state/last-session",
        last_username="alice",
        last_session_command="labwc",
        username_entry=StubEntry(""),
        password_entry=StubEntry("secret"),
        status_label=DummyLabel(),
    )
    app.refresh_sessions = lambda username="", preferred_command="": None
    app.get_session_command = lambda: "sway"

    monkeypatch.setattr(
        greeter.wldm.state,
        "save_last_session_file",
        lambda path, username, command: calls.append((path, username, command)),
    )

    greeter.GreeterApp.handle_event(
        app,
        {
            "v": 1,
            "type": "event",
            "event": greeter.greeter_protocol.EVENT_SESSION_FINISHED,
            "payload": {"pid": 1, "returncode": 0, "failed": False, "message": "Session finished."},
        },
    )

    assert calls == [("/tmp/wldm-state/last-session", "alice", "labwc")]


def test_login_click_remembers_selected_session_before_username_clear(monkeypatch):
    greeter = load_greeter_module(monkeypatch)

    app = new_greeter_app(
        greeter,
        username_entry=StubEntry("alice"),
        password_entry=StubEntry("secret"),
    )
    app.set_auth_state = lambda busy: setattr(app, "auth_in_progress", busy)
    app.update_auth_widgets = lambda: None
    app.set_status = lambda message, error=False: None
    app.get_session_command = lambda: "labwc"
    app.get_selected_session = lambda: {"desktop_names": ["labwc"]}
    app.send_recv_answer = lambda data: (
        {"ok": True, "payload": {"state": "pending", "message": {"style": "secret", "text": "Password:"}}}
        if data["action"] == greeter.greeter_protocol.ACTION_CREATE_SESSION else
        {"ok": True, "payload": {"state": "ready"}}
        if data["action"] == greeter.greeter_protocol.ACTION_CONTINUE_SESSION else
        {"ok": True, "payload": {}}
    )

    class DummySecret:
        def __len__(self):
            return 6

        def clear(self):
            return None

    monkeypatch.setattr(greeter.greeter_auth.gtk_ffi, "read_password_secret", lambda entry: DummySecret())

    greeter.GreeterApp.on_login_clicked(app)
    greeter.GreeterApp.on_login_clicked(app)
    greeter.GreeterApp.on_login_clicked(app)

    assert app.last_username == "alice"
    assert app.last_session_command == "labwc"
    assert app.username_entry.get_text() == ""


def test_set_status_toggles_error_css_class(monkeypatch):
    greeter = load_greeter_module(monkeypatch)

    app = new_greeter_app(greeter, status_label=StubStatusLabel())

    greeter.GreeterApp.set_status(app, "Authentication failed.", error=True)
    assert app.status_label.text == "Authentication failed."
    assert app.status_label.added == ["status-error"]

    greeter.GreeterApp.set_status(app, "Starting session...", error=False)
    assert app.status_label.text == "Starting session..."
    assert app.status_label.removed == ["status-error", "status-error"]


def test_on_clock_tick_polls_session_finished_event_and_reenables_inputs(monkeypatch):
    greeter = load_greeter_module(monkeypatch)

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
                "event": greeter.greeter_protocol.EVENT_SESSION_FINISHED,
                "payload": {"pid": 1, "returncode": 0, "failed": False, "message": "Session finished."},
            }

        def close(self):
            return None

    app = new_greeter_app(
        greeter,
        client=FakeClient(),
        auth_in_progress=True,
        username_entry=StubEntry("alice"),
        password_entry=StubEntry("secret"),
        login_button=DummyButton(),
        status_label=DummyLabel(),
    )

    assert greeter.GreeterApp.on_clock_tick(app) is True
    assert app.auth_in_progress is False
    assert app.username_entry.text == "alice"
    assert app.password_entry.text == ""
    assert app.username_entry.focused is True
    assert app.username_entry.sensitive is True
    assert app.password_entry.sensitive is False
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
            raise greeter.greeter_protocol.ProtocolError("broken frame", b"\x00\x01bad")

        def close(self):
            return None

    app = greeter.GreeterApp.__new__(greeter.GreeterApp)
    app.client = FakeClient()
    app.quit = False
    app.status_label = types.SimpleNamespace(set_text=lambda text: events.append(("status", text)))
    monkeypatch.setattr(app, "on_quit", lambda *args: events.append(("quit", True)))
    monkeypatch.setattr(
        greeter.logger,
        "critical",
        lambda msg, *args: events.append(("log", msg % args if args else msg)),
    )

    greeter.GreeterApp.poll_events(app)

    assert ("status", "Connection to daemon lost.") in events
    assert ("quit", True) in events
    assert any(item[0] == "log" and "raw=b'\\x00\\x01bad'" in item[1] for item in events)


def test_poll_events_returns_when_lock_is_busy(monkeypatch):
    greeter = load_greeter_module(monkeypatch)
    events = []

    class FakeClient:
        def can_read(self):
            events.append(("can_read", True))
            return True

    app = new_greeter_app(greeter, client=FakeClient())
    app.handle_connection_lost = lambda: events.append(("lost", True))

    class BusyLock:
        def acquire(self, blocking=False):
            assert blocking is False
            return False

        def release(self):
            events.append(("release", True))

    greeter.greeter_client.poll_events(app, BusyLock())

    assert events == []


def test_poll_events_treats_clean_eof_and_unexpected_errors_as_connection_loss(monkeypatch):
    greeter = load_greeter_module(monkeypatch)
    events = []

    class EofClient:
        def can_read(self):
            return True

        def read_message(self):
            return None

    app = new_greeter_app(greeter, client=EofClient())
    app.handle_connection_lost = lambda: events.append("lost")
    greeter.greeter_client.poll_events(app, greeter.threading.Lock())
    assert events == ["lost"]

    events.clear()

    class BrokenClient:
        def can_read(self):
            raise RuntimeError("boom")

    app = new_greeter_app(greeter, client=BrokenClient())
    app.handle_connection_lost = lambda: events.append("lost")
    monkeypatch.setattr(greeter.logger, "critical", lambda msg, *args: events.append(msg % args if args else msg))
    greeter.greeter_client.poll_events(app, greeter.threading.Lock())

    assert "lost" in events
    assert any("unexpected polling error" in item for item in events if isinstance(item, str))


def test_poll_events_ignores_unexpected_non_event_message(monkeypatch):
    greeter = load_greeter_module(monkeypatch)
    events = []

    class FakeClient:
        def __init__(self):
            self.reads = 0

        def can_read(self):
            return self.reads == 0

        def read_message(self):
            self.reads += 1
            return {"v": 1, "type": "response", "id": 1, "action": "noop", "ok": True, "payload": {}}

    app = new_greeter_app(greeter, client=FakeClient())
    app.handle_connection_lost = lambda: events.append("lost")
    monkeypatch.setattr(greeter.logger, "debug", lambda msg, *args: events.append(msg % args if args else msg))
    greeter.greeter_client.poll_events(app, greeter.threading.Lock())

    assert events == ["unexpected protocol message while idle: {'v': 1, 'type': 'response', 'id': 1, 'action': 'noop', 'ok': True, 'payload': {}}"]


def test_send_recv_answer_returns_empty_dict_on_bad_protocol(monkeypatch):
    greeter = load_greeter_module(monkeypatch)

    monkeypatch.setattr(greeter.wldm.sessions, "desktop_sessions", lambda username="": [])

    class FakeClient:
        def write_message(self, message):
            return None

        def read_message(self):
            raise greeter.greeter_protocol.ProtocolError("broken frame", b"\x00\x01bad")

        def can_read(self):
            return False

        def close(self):
            return None

    app = greeter.GreeterApp(client=FakeClient())

    request = greeter.greeter_protocol.new_request(greeter.greeter_protocol.ACTION_REBOOT, {})

    assert app.send_recv_answer(request) == {}
    assert request["action"] == greeter.greeter_protocol.ACTION_REBOOT


def test_send_recv_answer_treats_bad_protocol_as_connection_loss(monkeypatch):
    greeter = load_greeter_module(monkeypatch)

    monkeypatch.setattr(greeter.wldm.sessions, "desktop_sessions", lambda username="": [])
    events = []

    class FakeClient:
        def write_message(self, message):
            return None

        def read_message(self):
            raise greeter.greeter_protocol.ProtocolError("broken frame", b"\x00\x01bad")

        def can_read(self):
            return False

        def close(self):
            return None

    app = greeter.GreeterApp(client=FakeClient())
    app.status_label = types.SimpleNamespace(set_text=lambda text: events.append(("status", text)))
    monkeypatch.setattr(app, "on_quit", lambda *args: events.append(("quit", True)))
    monkeypatch.setattr(
        greeter.logger,
        "critical",
        lambda msg, *args: events.append(("log", msg % args if args else msg)),
    )

    request = greeter.greeter_protocol.new_request(greeter.greeter_protocol.ACTION_REBOOT, {})

    assert app.send_recv_answer(request) == {}
    assert ("status", "Connection to daemon lost.") in events
    assert ("quit", True) in events
    assert any(item[0] == "log" and "raw=b'\\x00\\x01bad'" in item[1] for item in events)


def test_send_recv_answer_treats_clean_eof_and_unexpected_errors_as_connection_loss(monkeypatch):
    greeter = load_greeter_module(monkeypatch)
    monkeypatch.setattr(greeter.wldm.sessions, "desktop_sessions", lambda username="": [])
    request = greeter.greeter_protocol.new_request(greeter.greeter_protocol.ACTION_REBOOT, {})
    events = []

    class EofClient:
        def write_message(self, message):
            return None

        def read_message(self):
            return None

    app = greeter.GreeterApp(client=EofClient())
    app.handle_connection_lost = lambda: events.append("lost")
    assert app.send_recv_answer(request) == {}
    assert events == ["lost"]

    events.clear()

    class BrokenClient:
        def write_message(self, message):
            raise RuntimeError("boom")

        def read_message(self):
            return None

    app = greeter.GreeterApp(client=BrokenClient())
    app.handle_connection_lost = lambda: events.append("lost")
    monkeypatch.setattr(greeter.logger, "critical", lambda msg, *args: events.append(msg % args if args else msg))
    assert app.send_recv_answer(request) == {}
    assert "lost" in events
    assert any("unexpected error" in item for item in events if isinstance(item, str))


def test_new_ipc_client_requires_socket_fd_env(monkeypatch):
    greeter = load_greeter_module(monkeypatch)

    monkeypatch.delenv("WLDM_SOCKET_FD", raising=False)

    try:
        greeter._new_ipc_client()
    except RuntimeError as exc:
        assert "WLDM_SOCKET_FD" in str(exc)
    else:
        raise AssertionError("_new_ipc_client() should have failed")


def test_new_ipc_client_uses_socket_fd(monkeypatch):
    greeter = load_greeter_module(monkeypatch)
    calls = []

    class FakeSocketClient:
        def __init__(self, fd):
            calls.append(fd)

    monkeypatch.setenv("WLDM_SOCKET_FD", "11")
    monkeypatch.setattr(greeter, "_SocketClient", FakeSocketClient)

    greeter._new_ipc_client()

    assert calls == [11]


def test_on_login_clicked_sets_failure_and_clears_password(monkeypatch):
    greeter = load_greeter_module(monkeypatch)

    monkeypatch.setattr(greeter.wldm.sessions, "desktop_sessions", lambda username="": [])

    app = greeter.GreeterApp(client=DummyClient())
    app.username_entry = StubEntry("alice")
    app.password_entry = StubEntry("secret")
    app.status_label = DummyLabel()
    app.sessions = [{"name": "Default", "command": "start-session", "comment": "Default session", "desktop_names": ["default"]}]
    app.sessions_entry = selected_entry("Default")
    app.conversation_pending = True
    app.conversation_prompt_style = "secret"
    app.conversation_prompt_text = "Password:"
    app.session_ready = False
    app.auth_username = "alice"
    monkeypatch.setattr(app, "send_recv_answer", lambda data: {"ok": False})

    app.on_login_clicked()

    assert app.status_label.text == "Authentication failed."
    assert app.username_entry.text == "alice"
    assert app.password_entry.text == ""
    assert app.password_entry.focused is True


def test_on_login_clicked_restarts_password_prompt_after_auth_failure(monkeypatch):
    greeter = load_greeter_module(monkeypatch)

    monkeypatch.setattr(greeter.wldm.sessions, "desktop_sessions", lambda username="": [])

    app = greeter.GreeterApp(client=DummyClient())
    app.username_entry = StubEntry("alice")
    app.password_entry = StubEntry("wrong")
    app.status_label = DummyLabel()
    app.sessions = [{"name": "Default", "command": "start-session", "comment": "Default session", "desktop_names": ["default"]}]
    app.sessions_entry = selected_entry("Default")
    app.conversation_pending = True
    app.conversation_prompt_style = "secret"
    app.conversation_prompt_text = "Password:"
    app.session_ready = False
    app.auth_username = "alice"
    sent: list[str] = []

    def fake_send_recv_answer(data):
        sent.append(data["action"])

        if data["action"] == greeter.greeter_protocol.ACTION_CONTINUE_SESSION:
            return {"ok": False, "error": {"code": "auth_retryable", "message": "Authentication failed."}}

        if data["action"] == greeter.greeter_protocol.ACTION_CREATE_SESSION:
            return {"ok": True, "payload": {"state": "pending", "message": {"style": "secret", "text": "Password:"}}}

        raise AssertionError(f"unexpected action {data['action']}")

    monkeypatch.setattr(app, "send_recv_answer", fake_send_recv_answer)

    app.on_login_clicked()

    assert sent == [
        greeter.greeter_protocol.ACTION_CONTINUE_SESSION,
        greeter.greeter_protocol.ACTION_CREATE_SESSION,
    ]
    assert app.status_label.text == "Authentication failed."
    assert app.conversation_pending is True
    assert app.conversation_prompt_style == "secret"
    assert app.password_entry.text == ""
    assert app.password_entry.focused is True


def test_on_login_clicked_includes_desktop_names_in_auth_request(monkeypatch):
    greeter = load_greeter_module(monkeypatch)
    monkeypatch.setattr(greeter.wldm.sessions, "desktop_sessions", lambda username="": [])

    app = greeter.GreeterApp(client=DummyClient())
    app.username_entry = StubEntry("alice")
    app.password_entry = StubEntry("secret")
    app.status_label = types.SimpleNamespace(set_text=lambda text: None)
    app.sessions = [{"name": "Sway", "command": "sway", "comment": "Sway", "desktop_names": ["sway", "wlroots"]}]
    app.sessions_entry = selected_entry("Sway")
    sent = []

    def fake_send_recv_answer(data):
        sent.append(data)
        if data["action"] == greeter.greeter_protocol.ACTION_CREATE_SESSION:
            return {"ok": True, "payload": {"state": "pending", "message": {"style": "secret", "text": "Password:"}}}
        if data["action"] == greeter.greeter_protocol.ACTION_CONTINUE_SESSION:
            return {"ok": True, "payload": {"state": "ready"}}
        if data["action"] == greeter.greeter_protocol.ACTION_START_SESSION:
            return {"ok": True, "payload": {}}
        raise AssertionError(f"unexpected action {data['action']}")

    monkeypatch.setattr(app, "send_recv_answer", fake_send_recv_answer)

    app.on_login_clicked()
    app.password_entry.set_text("secret")
    app.on_login_clicked()
    app.on_login_clicked()

    assert sent[-1]["action"] == greeter.greeter_protocol.ACTION_START_SESSION
    assert sent[-1]["payload"]["desktop_names"] == ["sway", "wlroots"]


def test_on_login_clicked_rejects_overlong_username(monkeypatch):
    greeter = load_greeter_module(monkeypatch)
    monkeypatch.setattr(greeter.wldm.sessions, "desktop_sessions", lambda username="": [])

    app = greeter.GreeterApp(client=DummyClient())
    app.username_entry = StubEntry("a" * 257)
    app.password_entry = StubEntry("secret")
    app.status_label = DummyLabel()
    monkeypatch.setattr(app, "send_recv_answer", lambda data: (_ for _ in ()).throw(AssertionError("unexpected send")))

    app.on_login_clicked()

    assert app.status_label.text == (
        f"Username must be {greeter.greeter_protocol.AUTH_FIELD_MAX_LENGTH} bytes or less."
    )


def test_on_login_clicked_rejects_overlong_password(monkeypatch):
    greeter = load_greeter_module(monkeypatch)
    monkeypatch.setattr(greeter.wldm.sessions, "desktop_sessions", lambda username="": [])

    app = greeter.GreeterApp(client=DummyClient())
    app.username_entry = StubEntry("alice")
    app.password_entry = StubEntry("a" * 257)
    app.status_label = DummyLabel()
    app.conversation_pending = True
    app.conversation_prompt_style = "secret"
    app.conversation_prompt_text = "Password:"
    app.session_ready = False
    monkeypatch.setattr(app, "send_recv_answer", lambda data: (_ for _ in ()).throw(AssertionError("unexpected send")))

    app.on_login_clicked()

    assert app.status_label.text == (
        f"Response must be {greeter.greeter_protocol.AUTH_FIELD_MAX_LENGTH} bytes or less."
    )
    assert app.password_entry.focused is True


def test_on_login_clicked_sets_success_message_and_clears_username(monkeypatch):
    greeter = load_greeter_module(monkeypatch)

    monkeypatch.setattr(greeter.wldm.sessions, "desktop_sessions", lambda username="": [])

    app = greeter.GreeterApp(client=DummyClient())
    app.username_entry = StubEntry("alice")
    app.password_entry = StubEntry("secret")
    app.status_label = DummyLabel()
    app.sessions = [{"name": "Sway", "command": "sway", "comment": "Sway", "desktop_names": ["sway"]}]
    app.sessions_entry = selected_entry("Sway")
    monkeypatch.setattr(
        app,
        "send_recv_answer",
        lambda data: (
            {"ok": True, "payload": {"state": "pending", "message": {"style": "secret", "text": "Password:"}}}
            if data["action"] == greeter.greeter_protocol.ACTION_CREATE_SESSION else
            {"ok": True, "payload": {"state": "ready"}}
            if data["action"] == greeter.greeter_protocol.ACTION_CONTINUE_SESSION else
            {"ok": True, "payload": {}}
        ),
    )

    app.on_login_clicked()
    app.password_entry.set_text("secret")
    app.on_login_clicked()
    assert app.status_label.text == "Authentication accepted. Select a session."
    app.on_login_clicked()

    assert app.status_label.text == "Authentication accepted. Waiting for session..."
    assert app.last_username == "alice"
    assert app.username_entry.text == ""
    assert app.password_entry.text == ""


def test_read_prompt_response_returns_none_without_password_entry(monkeypatch):
    greeter = load_greeter_module(monkeypatch)
    app = greeter.GreeterApp.__new__(greeter.GreeterApp)
    app.password_entry = None
    app.conversation_prompt_style = "secret"
    app.conversation_prompt_text = "Password:"

    assert greeter.GreeterApp.read_prompt_response(app) is None


def test_read_prompt_response_returns_empty_secret_for_info_prompt(monkeypatch):
    greeter = load_greeter_module(monkeypatch)
    app = new_greeter_app(
        greeter,
        password_entry=StubEntry("ignored"),
        conversation_prompt_style="info",
        conversation_prompt_text="Info",
    )

    response = greeter.GreeterApp.read_prompt_response(app)

    assert response is not None
    assert response.as_bytes() == b""
    assert app.password_entry.text == ""


def test_read_prompt_response_rejects_empty_secret_prompt(monkeypatch):
    greeter = load_greeter_module(monkeypatch)
    app = new_greeter_app(
        greeter,
        password_entry=StubEntry(),
        conversation_prompt_style="secret",
        conversation_prompt_text="Password:",
        status_label=DummyLabel(),
    )

    class EmptySecret:
        def __len__(self):
            return 0

        def clear(self):
            return None

    monkeypatch.setattr(greeter.greeter_auth.gtk_ffi, "read_password_secret", lambda entry: EmptySecret())

    assert greeter.GreeterApp.read_prompt_response(app) is None
    assert app.status_label.text == "Password:"
    assert app.password_entry.focused is True


def test_start_selected_session_sends_start_request(monkeypatch):
    greeter = load_greeter_module(monkeypatch)
    app = new_greeter_app(greeter)
    sent = {}
    app.send_recv_answer = lambda data: sent.update(data) or {"ok": True, "payload": {}}

    assert greeter.GreeterApp.start_selected_session(app, "sway", ["sway", "wlroots"]) is True
    assert sent["action"] == greeter.greeter_protocol.ACTION_START_SESSION
    assert sent["payload"] == {"command": "sway", "desktop_names": ["sway", "wlroots"]}


def test_handle_conversation_answer_sets_pending_prompt(monkeypatch):
    greeter = load_greeter_module(monkeypatch)
    app = new_greeter_app(
        greeter,
        password_entry=types.SimpleNamespace(set_text=lambda text: None, grab_focus=lambda: None),
        status_label=DummyLabel(),
        auth_username="alice",
    )

    result = greeter.GreeterApp.handle_conversation_answer(
        app,
        {"ok": True, "payload": {"state": "pending", "message": {"style": "visible", "text": "Code:"}}},
    )

    assert result == "pending"
    assert app.conversation_pending is True
    assert app.conversation_prompt_style == "visible"
    assert app.status_label.text == ""


def test_handle_conversation_answer_marks_session_ready(monkeypatch):
    greeter = load_greeter_module(monkeypatch)
    app = new_greeter_app(
        greeter,
        status_label=DummyLabel(),
        conversation_pending=True,
    )
    app.conversation_prompt_style = "secret"
    app.conversation_prompt_text = "Password:"
    app.session_ready = False
    app.auth_username = "alice"

    result = greeter.GreeterApp.handle_conversation_answer(app, {"ok": True, "payload": {"state": "ready"}})

    assert result == "ready"
    assert app.session_ready is True
    assert app.conversation_pending is False
    assert app.status_label.text == "Authentication accepted. Select a session."


def test_handle_conversation_answer_rejects_invalid_style(monkeypatch):
    greeter = load_greeter_module(monkeypatch)
    app = greeter.GreeterApp.__new__(greeter.GreeterApp)
    app.password_entry = None
    app.status_label = DummyLabel()
    app.username_entry = None
    app.sessions_entry = None
    app.login_button = None
    app.auth_in_progress = False
    app.conversation_pending = False
    app.session_ready = False
    app.auth_username = "alice"
    warnings = []

    monkeypatch.setattr(greeter.logger, "warning", lambda msg, *args: warnings.append(msg % args if args else msg))

    result = greeter.GreeterApp.handle_conversation_answer(
        app,
        {"ok": True, "payload": {"state": "pending", "message": {"style": "otp", "text": "Code:"}}},
    )

    assert result == "failed"
    assert app.conversation_pending is False
    assert any("unsupported auth conversation step" in item for item in warnings)
def test_handle_conversation_answer_rejects_unsuccessful_reply(monkeypatch):
    greeter = load_greeter_module(monkeypatch)
    app = greeter.GreeterApp.__new__(greeter.GreeterApp)
    cleared = []
    statuses = []
    app.clear_conversation_state = lambda: cleared.append(True)
    app.set_status = lambda text, error=False: statuses.append((text, error))

    result = greeter.GreeterApp.handle_conversation_answer(
        app,
        {"ok": False, "error": {"message": "Account locked"}},
    )

    assert result == "failed"
    assert cleared == [True]
    assert statuses == [("Account locked", True)]


def test_handle_conversation_answer_rejects_unexpected_state(monkeypatch):
    greeter = load_greeter_module(monkeypatch)
    app = greeter.GreeterApp.__new__(greeter.GreeterApp)
    cleared = []
    warnings = []
    app.clear_conversation_state = lambda: cleared.append(True)
    app.set_status = lambda text, error=False: warnings.append(f"status:{text}:{error}")

    monkeypatch.setattr(greeter.logger, "warning", lambda msg, *args: warnings.append(msg % args if args else msg))

    result = greeter.GreeterApp.handle_conversation_answer(
        app,
        {"ok": True, "payload": {"state": "mystery"}},
    )

    assert result == "failed"
    assert cleared == [True]
    assert any("unexpected auth conversation state" in item for item in warnings)
    assert any(item == "status:Authentication failed.:True" for item in warnings)


def test_update_auth_widgets_for_initial_stage(monkeypatch):
    greeter = load_greeter_module(monkeypatch)

    app = new_greeter_app(
        greeter,
        username_entry=StubEntry(),
        password_entry=StubEntry(),
        sessions_entry=StubEntry(),
        login_button=DummyButton(),
        cancel_button=DummyButton(),
    )

    greeter.GreeterApp.update_auth_widgets(app)

    assert app.username_entry.sensitive is True
    assert app.password_entry.sensitive is False
    assert app.password_entry.visible is False
    assert app.password_entry.visibility is True
    assert app.password_entry.show_peek_icon is False
    assert app.password_entry.placeholder_text == ""
    assert app.sessions_entry.sensitive is False
    assert app.sessions_entry.visible is False
    assert app.login_button.sensitive is True
    assert app.login_button.label == "Next"
    assert app.cancel_button.visible is False
    assert app.cancel_button.sensitive is True
def test_set_conversation_prompt_updates_visible_prompt_widgets(monkeypatch):
    greeter = load_greeter_module(monkeypatch)

    app = new_greeter_app(
        greeter,
        username_entry=StubEntry(),
        password_entry=StubEntry("old"),
        sessions_entry=StubEntry(),
        login_button=DummyButton(),
        cancel_button=DummyButton(),
        status_label=DummyLabel(),
    )

    greeter.GreeterApp.set_conversation_prompt(app, "visible", "Verification code")

    assert app.conversation_pending is True
    assert app.password_entry.text == ""
    assert app.password_entry.focused is True
    assert app.password_entry.sensitive is True
    assert app.password_entry.visible is True
    assert app.password_entry.visibility is True
    assert app.password_entry.show_peek_icon is True
    assert app.password_entry.placeholder_text == "Verification code"
    assert app.sessions_entry.visible is False
    assert app.login_button.label == "Continue"
    assert app.cancel_button.visible is True
    assert app.cancel_button.sensitive is True
    assert app.status_label.text == ""


def test_set_conversation_prompt_hides_entry_for_info_prompt(monkeypatch):
    greeter = load_greeter_module(monkeypatch)

    app = new_greeter_app(
        greeter,
        username_entry=StubEntry(),
        password_entry=StubEntry("old"),
        sessions_entry=StubEntry(),
        login_button=DummyButton(),
        cancel_button=DummyButton(),
        status_label=DummyLabel(),
    )

    greeter.GreeterApp.set_conversation_prompt(app, "info", "Use your hardware token")

    assert app.password_entry.text == ""
    assert app.password_entry.focused is False
    assert app.password_entry.sensitive is False
    assert app.password_entry.visible is False
    assert app.password_entry.show_peek_icon is False
    assert app.password_entry.placeholder_text == ""
    assert app.login_button.label == "Continue"
    assert app.cancel_button.visible is True
    assert app.cancel_button.sensitive is True
    assert app.status_label.text == "Use your hardware token"


def test_set_conversation_prompt_marks_error_prompt_as_error(monkeypatch):
    greeter = load_greeter_module(monkeypatch)

    app = new_greeter_app(
        greeter,
        username_entry=StubEntry(),
        password_entry=StubEntry("old"),
        sessions_entry=StubEntry(),
        login_button=DummyButton(),
        cancel_button=DummyButton(),
        status_label=StubStatusLabel(),
    )

    greeter.GreeterApp.set_conversation_prompt(app, "error", "Authentication failed")

    assert app.password_entry.visible is False
    assert app.password_entry.focused is False
    assert app.password_entry.show_peek_icon is False
    assert app.login_button.label == "Continue"
    assert app.cancel_button.visible is True
    assert app.cancel_button.sensitive is True
    assert app.status_label.text == "Authentication failed"
    assert app.status_label.added == ["status-error"]


def test_set_session_ready_updates_post_auth_widgets(monkeypatch):
    greeter = load_greeter_module(monkeypatch)

    app = new_greeter_app(
        greeter,
        conversation_pending=True,
        conversation_prompt_style="secret",
        conversation_prompt_text="Password:",
        username_entry=StubEntry(),
        password_entry=StubEntry(),
        sessions_entry=StubEntry(),
        login_button=DummyButton(),
        cancel_button=DummyButton(),
        status_label=DummyLabel(),
    )

    greeter.GreeterApp.set_session_ready(app)

    assert app.conversation_pending is False
    assert app.session_ready is True
    assert app.password_entry.visible is False
    assert app.password_entry.show_peek_icon is False
    assert app.sessions_entry.sensitive is True
    assert app.sessions_entry.visible is True
    assert app.login_button.label == "Start session"
    assert app.cancel_button.visible is True
    assert app.cancel_button.sensitive is True
    assert app.status_label.text == "Authentication accepted. Select a session."


def test_on_cancel_clicked_cancels_pending_auth_and_restores_username(monkeypatch):
    greeter = load_greeter_module(monkeypatch)

    app = new_greeter_app(
        greeter,
        conversation_pending=True,
        auth_username="alice",
        last_session_command="sway",
        username_entry=StubEntry(""),
        password_entry=StubEntry("secret"),
    )
    app.set_status = lambda text, error=False: None
    app.set_auth_state = lambda value: None
    app.clear_conversation_state = lambda: (
        setattr(app, "conversation_pending", False),
        setattr(app, "conversation_prompt_style", ""),
        setattr(app, "conversation_prompt_text", ""),
        setattr(app, "session_ready", False),
        setattr(app, "auth_username", ""),
    )
    calls = []
    refreshed = []
    updated = []
    monkeypatch.setattr(
        greeter.GreeterApp,
        "clear_username_selection",
        lambda self: calls.append(("clear-selection", self.username_entry.text)),
    )
    app.send_recv_answer = lambda data: calls.append(data) or {"ok": True}
    app.refresh_sessions = lambda username, preferred_command="": refreshed.append((username, preferred_command))
    app.update_identity_preview = lambda: updated.append(True)

    greeter.GreeterApp.on_cancel_clicked(app)

    assert calls[0]["type"] == "request"
    assert calls[0]["action"] == greeter.greeter_protocol.ACTION_CANCEL_SESSION
    assert app.username_entry.text == "alice"
    assert app.username_entry.focused is True
    assert app.password_entry.text == ""
    assert refreshed == [("alice", "sway")]
    assert updated == [True]


def test_on_login_clicked_starts_selected_session_after_ready(monkeypatch):
    greeter = load_greeter_module(monkeypatch)

    class FakeEntry:
        def __init__(self, text=""):
            self.text = text

        def get_text(self):
            return self.text

        def set_text(self, text):
            self.text = text

    app = greeter.GreeterApp(client=DummyClient())
    app.username_entry = FakeEntry("alice")
    app.password_entry = FakeEntry("")
    app.status_label = DummyLabel()
    app.auth_username = "alice"
    app.session_ready = True
    app.conversation_pending = False
    app.sessions = [{"name": "Sway", "command": "sway", "comment": "Sway", "desktop_names": ["sway"]}]
    app.sessions_entry = types.SimpleNamespace(
        get_selected_item=lambda: types.SimpleNamespace(get_string=lambda: "Sway")
    )
    monkeypatch.setattr(app, "send_recv_answer", lambda data: {"ok": True, "payload": {}})

    app.on_login_clicked()

    assert app.status_label.text == "Authentication accepted. Waiting for session..."
    assert app.last_username == "alice"
    assert app.last_session_command == "sway"
    assert app.username_entry.text == ""


def test_on_login_clicked_returns_early_without_entries(monkeypatch):
    greeter = load_greeter_module(monkeypatch)
    app = greeter.GreeterApp.__new__(greeter.GreeterApp)
    app.username_entry = None
    app.password_entry = None
    app.auth_in_progress = False

    assert greeter.GreeterApp.on_login_clicked(app) is None


def test_on_login_clicked_returns_early_when_auth_is_in_progress(monkeypatch):
    greeter = load_greeter_module(monkeypatch)
    app = greeter.GreeterApp.__new__(greeter.GreeterApp)
    app.username_entry = types.SimpleNamespace()
    app.password_entry = types.SimpleNamespace()
    app.auth_in_progress = True

    assert greeter.GreeterApp.on_login_clicked(app) is None


def test_on_login_clicked_reports_failed_session_start_after_ready(monkeypatch):
    greeter = load_greeter_module(monkeypatch)

    class FakeEntry:
        def __init__(self, text=""):
            self.text = text

        def get_text(self):
            return self.text

        def set_text(self, text):
            self.text = text

    app = greeter.GreeterApp(client=DummyClient())
    app.username_entry = FakeEntry("alice")
    app.password_entry = FakeEntry("")
    app.status_label = DummyLabel()
    app.auth_username = "alice"
    app.session_ready = True
    app.conversation_pending = False
    app.sessions = [{"name": "Sway", "command": "sway", "comment": "Sway", "desktop_names": ["sway"]}]
    app.sessions_entry = types.SimpleNamespace(
        get_selected_item=lambda: types.SimpleNamespace(get_string=lambda: "Sway")
    )
    monkeypatch.setattr(app, "send_recv_answer", lambda data: {"ok": False, "payload": {}})

    app.on_login_clicked()

    assert app.status_label.text == "Unable to start session."
    assert app.auth_in_progress is False
def test_cmd_main_validates_resources_path(monkeypatch, tmp_path):
    greeter = load_greeter_module(monkeypatch)

    monkeypatch.delenv("WLDM_DATA_DIR", raising=False)
    assert greeter.cmd_main(types.SimpleNamespace()) == greeter.wldm.EX_FAILURE

    data_dir = tmp_path
    (data_dir / "resources").mkdir()
    monkeypatch.setenv("WLDM_DATA_DIR", str(data_dir))
    monkeypatch.setenv("WLDM_SOCKET_FD", "9")
    monkeypatch.setattr(greeter.os.path, "isfile", lambda path: False)

    run_calls = []

    class FakeGreeterApp:
        def __init__(self):
            run_calls.append(("init",))

        def run(self):
            run_calls.append(("run",))

    monkeypatch.setattr(greeter, "GreeterApp", FakeGreeterApp)

    assert greeter.cmd_main(types.SimpleNamespace()) == greeter.wldm.EX_SUCCESS
    assert run_calls == [("init",), ("run",)]


def test_default_resource_path_uses_installed_share_when_env_is_missing(monkeypatch, tmp_path):
    greeter = load_greeter_module(monkeypatch)

    monkeypatch.setenv("WLDM_DATA_DIR", str(tmp_path / "share" / "wldm"))

    assert greeter._default_resource_path() == str(tmp_path / "share" / "wldm" / "resources")


def test_default_resource_path_is_empty_without_resource_env_or_data_dir(monkeypatch):
    greeter = load_greeter_module(monkeypatch)

    monkeypatch.delenv("WLDM_DATA_DIR", raising=False)

    assert greeter._default_resource_path() == ""


def test_themed_resource_path_uses_default_theme(monkeypatch, tmp_path):
    greeter = load_greeter_module(monkeypatch)

    monkeypatch.setenv("WLDM_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("WLDM_THEME", "default")

    assert greeter._themed_resource_path() == str(tmp_path / "resources")


def test_themed_resource_path_uses_named_theme_when_present(monkeypatch, tmp_path):
    greeter = load_greeter_module(monkeypatch)
    base = tmp_path / "resources"
    theme_dir = tmp_path / "themes" / "retro"
    base.mkdir()
    theme_dir.mkdir(parents=True)

    monkeypatch.setenv("WLDM_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("WLDM_THEME", "retro")

    assert greeter._themed_resource_path() == str(theme_dir)


def test_themed_resource_path_falls_back_to_default_when_theme_is_missing(monkeypatch, tmp_path):
    greeter = load_greeter_module(monkeypatch)
    base = tmp_path / "resources"
    base.mkdir()
    warnings = []

    monkeypatch.setenv("WLDM_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("WLDM_THEME", "missing")
    monkeypatch.setattr(greeter.logger, "warning", lambda msg, *args: warnings.append(msg % args if args else msg))

    assert greeter._themed_resource_path() == str(base)
    assert any("falling back to default" in message for message in warnings)


def test_greeter_locale_path_prefers_theme_locale(monkeypatch, tmp_path):
    greeter = load_greeter_module(monkeypatch)
    theme_dir = tmp_path / "themes" / "retro"
    locale_dir = theme_dir / "locale"
    locale_dir.mkdir(parents=True)
    greeter.resource_path = str(theme_dir)
    monkeypatch.delenv("WLDM_LOCALE_DIR", raising=False)

    assert greeter._greeter_locale_path() == str(locale_dir)


def test_greeter_locale_path_prefers_locale_dir(monkeypatch, tmp_path):
    greeter = load_greeter_module(monkeypatch)
    greeter.resource_path = str(tmp_path / "resources")
    monkeypatch.setenv("WLDM_LOCALE_DIR", str(tmp_path / "locale"))

    assert greeter._greeter_locale_path() == str(tmp_path / "locale")


def test_setup_greeter_logging_installs_file_logger_and_excepthook(monkeypatch):
    greeter = load_greeter_module(monkeypatch)

    greeter._setup_greeter_logging()

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

    greeter._setup_greeter_i18n()

    assert bind_calls == [("wldm", str(locale_dir))]
    assert textdomain_calls == ["wldm"]


def test_collect_theme_widgets_rejects_missing_required_widgets(monkeypatch):
    greeter = load_greeter_module(monkeypatch)
    monkeypatch.setenv("WLDM_THEME", "retro")
    app = greeter.GreeterApp(client=DummyClient())

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
    app = greeter.GreeterApp(client=DummyClient())
    builder = StubBuilder(
        {
            "main_window": StubWindow(),
            "username_entry": StubEntry(),
            "password_entry": object(),
            "login_button": DummyButton(),
        }
    )

    try:
        app.collect_theme_widgets(builder)
    except RuntimeError as exc:
        assert "retro" in str(exc)
        assert "password_entry" in str(exc)
    else:
        raise AssertionError("collect_theme_widgets() should reject invalid required widgets")


def test_on_activate_falls_back_to_default_theme_when_theme_ui_is_invalid(monkeypatch, tmp_path):
    greeter = load_greeter_module(monkeypatch)
    greeter.resource_path = str(tmp_path / "themes" / "retro")
    fallback_path = str(tmp_path / "resources")
    warnings = []
    i18n_calls = []
    objects = make_activate_objects()
    window = objects["main_window"]
    loaded_paths = []
    builders = iter([
        StubBuilder(loaded_paths=loaded_paths, add_error=RuntimeError("broken themed greeter.ui")),
        StubBuilder(objects, loaded_paths=loaded_paths),
    ])
    monkeypatch.setattr(greeter.Gtk.Builder, "new", lambda: next(builders))
    monkeypatch.setattr(greeter, "_greeter_theme", lambda: "retro")
    monkeypatch.setattr(greeter, "_default_resource_path", lambda: fallback_path)
    monkeypatch.setattr(greeter, "_setup_greeter_i18n", lambda: i18n_calls.append("i18n"))
    monkeypatch.setattr(greeter.logger, "warning", lambda msg, *args: warnings.append(msg % args if args else msg))
    monkeypatch.setattr(greeter.wldm.sessions, "desktop_sessions", lambda username="": [])
    monkeypatch.setenv("WLDM_ACTIONS", "")

    app = greeter.GreeterApp(client=DummyClient())
    app.on_activate(app.app)

    assert loaded_paths == [
        str(tmp_path / "themes" / "retro" / "greeter.ui"),
        str(tmp_path / "resources" / "greeter.ui"),
    ]
    assert greeter.resource_path == fallback_path
    assert i18n_calls == ["i18n"]
    assert any("falling back to default" in message for message in warnings)
    assert window.application is app.app
    assert window.presented is True


def test_on_activate_falls_back_to_default_theme_when_required_widgets_are_invalid(monkeypatch, tmp_path):
    greeter = load_greeter_module(monkeypatch)
    greeter.resource_path = str(tmp_path / "themes" / "retro")
    fallback_path = str(tmp_path / "resources")
    warnings = []
    i18n_calls = []
    invalid_objects = {
        "main_window": StubWindow(),
        "username_entry": StubEntry(),
        "password_entry": object(),
        "login_button": DummyButton(),
    }
    working_objects = make_activate_objects()
    builders = iter([StubBuilder(invalid_objects), StubBuilder(working_objects)])
    monkeypatch.setattr(greeter.Gtk.Builder, "new", lambda: next(builders))
    monkeypatch.setattr(greeter, "_greeter_theme", lambda: "retro")
    monkeypatch.setattr(greeter, "_default_resource_path", lambda: fallback_path)
    monkeypatch.setattr(greeter, "_setup_greeter_i18n", lambda: i18n_calls.append("i18n"))
    monkeypatch.setattr(greeter.logger, "warning", lambda msg, *args: warnings.append(msg % args if args else msg))
    monkeypatch.setattr(greeter.wldm.sessions, "desktop_sessions", lambda username="": [])
    monkeypatch.setenv("WLDM_ACTIONS", "")

    app = greeter.GreeterApp(client=DummyClient())
    app.on_activate(app.app)

    assert greeter.resource_path == fallback_path
    assert i18n_calls == ["i18n"]
    assert any("falling back to default" in message for message in warnings)
def test_on_activate_binds_widgets_and_populates_sessions(monkeypatch):
    greeter = load_greeter_module(monkeypatch)
    greeter.resource_path = "/tmp/resources"
    objects = make_activate_objects()
    window = objects["main_window"]
    username_entry = objects["username_entry"]
    password_entry = objects["password_entry"]
    sessions_entry = objects["sessions_entry"]
    login_button = objects["login_button"]
    cancel_button = objects["cancel_button"]
    quit_button = objects["quit_button"]
    reboot_button = objects["reboot_button"]
    suspend_button = objects["suspend_button"]
    hibernate_button = objects["hibernate_button"]

    monkeypatch.setattr(greeter.wldm.sessions, "desktop_sessions",
                        lambda username="": [
                            {"name": "Alpha", "command": "alpha", "comment": "Alpha session", "desktop_names": ["alpha"]},
                            {"name": "Beta", "command": "beta", "comment": "Beta session", "desktop_names": ["beta"]},
                        ])
    monkeypatch.setenv("WLDM_ACTIONS", "poweroff:reboot")
    monkeypatch.setattr(greeter.Gtk.Builder, "new", lambda: StubBuilder(objects))

    app = greeter.GreeterApp(client=DummyClient())
    app.on_activate(app.app)

    assert window.application is app.app
    assert window.default_widget is login_button
    assert window.presented is True
    assert sessions_entry.model.items == ["Alpha", "Beta"]
    assert sessions_entry.selected == 0
    assert login_button.connections == [("clicked", app.on_login_clicked)]
    assert cancel_button.connections == [("clicked", app.on_cancel_clicked)]
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
        ("activate", app.on_login_clicked),
    ]
    assert username_entry.connections == [
        ("changed", app.on_username_changed),
        ("activate", app.on_username_activate),
    ]
    assert greeter._test_timeout_calls == [(1, app.on_clock_tick)]  # type: ignore[attr-defined]


def test_username_activate_moves_focus_to_password(monkeypatch):
    greeter = load_greeter_module(monkeypatch)
    app = new_greeter_app(greeter, password_entry=StubEntry())
    del app.username_entry

    greeter.GreeterApp.on_username_activate(app)

    assert app.password_entry.focused is True


def test_cmd_main_loads_css_when_present(monkeypatch, tmp_path):
    greeter = load_greeter_module(monkeypatch)
    resource_dir = tmp_path / "resources"
    resource_dir.mkdir()
    css_path = resource_dir / "style.css"
    css_path.write_text("label {}", encoding="utf-8")

    monkeypatch.setenv("WLDM_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("WLDM_SOCKET_FD", "9")
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

    class FakeGreeterApp:
        def __init__(self):
            run_calls.append(("init",))

        def run(self):
            run_calls.append(("run",))

    monkeypatch.setattr(greeter, "GreeterApp", FakeGreeterApp)

    assert greeter.cmd_main(types.SimpleNamespace()) == greeter.wldm.EX_SUCCESS
    assert css_loaded == [str(css_path)]
    assert len(provider_calls) == 1
    assert run_calls == [("init",), ("run",)]


def test_system_action_buttons_send_requests(monkeypatch):
    greeter = load_greeter_module(monkeypatch)
    monkeypatch.setattr(greeter.wldm.sessions, "desktop_sessions", lambda username="": [])

    app = greeter.GreeterApp(client=DummyClient())
    app.status_label = DummyLabel()
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
        greeter.greeter_protocol.ACTION_REBOOT,
        greeter.greeter_protocol.ACTION_POWEROFF,
        greeter.greeter_protocol.ACTION_SUSPEND,
        greeter.greeter_protocol.ACTION_HIBERNATE,
    ]


def test_username_change_updates_identity_preview(monkeypatch):
    greeter = load_greeter_module(monkeypatch)
    greeter_account = importlib.import_module("wldm.greeter_account")
    calls = []
    monkeypatch.setattr(greeter_account, "account_service_profile",
                        lambda username: {"display_name": "Alice Doe", "avatar_path": ""})
    monkeypatch.setattr(greeter.wldm.sessions, "desktop_sessions",
                        lambda username="": calls.append(username) or [
                            {"name": "Sway", "command": "sway --debug", "comment": "User sway", "desktop_names": ["sway", "wlroots"]},
                        ])

    app = new_greeter_app(
        greeter,
        username_entry=StubEntry("alice"),
        identity_preview=StubBox(),
        identity_label=DummyLabel(),
        avatar_label=DummyLabel(),
    )

    greeter.GreeterApp.on_username_changed(app)

    assert app.identity_label.text == "Alice Doe"
    assert app.avatar_label.text == "A"
    assert app.identity_preview.visible is True
    assert calls == ["alice"]


def test_username_change_hides_identity_preview_without_accountsservice_profile(monkeypatch):
    greeter = load_greeter_module(monkeypatch)
    greeter_account = importlib.import_module("wldm.greeter_account")
    monkeypatch.setattr(greeter_account, "account_service_profile", lambda username: None)
    monkeypatch.setattr(greeter.wldm.sessions, "desktop_sessions", lambda username="": [])

    app = new_greeter_app(
        greeter,
        username_entry=StubEntry("alice"),
        identity_preview=StubBox(),
        identity_label=DummyLabel(),
        avatar_label=DummyLabel(),
    )

    greeter.GreeterApp.on_username_changed(app)

    assert app.identity_preview.visible is False
    assert app.identity_label.text is None
    assert app.avatar_label.text is None
