# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026  Alexey Gladkov <legion@kernel.org>

import types

import wldm
import wldm.greeter.curses.app as curses_greeter


class DummyClient:
    def write_message(self, _message):
        return None

    def read_message(self):
        return None

    def can_read(self):
        return False

    def close(self):
        return None


class FakeScreen:
    def __init__(self):
        self.calls = []

    def getmaxyx(self):
        return (24, 80)

    def addstr(self, *args):
        self.calls.append(("addstr", args))

    def addch(self, *args):
        self.calls.append(("addch", args))

    def erase(self):
        self.calls.append(("erase",))

    def refresh(self):
        self.calls.append(("refresh",))

    def nodelay(self, value):
        self.calls.append(("nodelay", value))

    def keypad(self, value):
        self.calls.append(("keypad", value))

    def getch(self):
        return ord("q")


def rendered_text(screen):
    return [call[1][2] for call in screen.calls if call[0] == "addstr"]


def rendered_contains(screen, text):
    return any(text in item for item in rendered_text(screen))


def rendered_chars(screen):
    return [call[1][2] for call in screen.calls if call[0] == "addch"]


def addstr_calls(screen):
    return [call[1] for call in screen.calls if call[0] == "addstr"]


def test_text_entry_edits_text():
    entry = curses_greeter.TextEntry("al")

    entry.grab_focus()
    entry.append("i")
    entry.backspace()
    entry.append("e")

    assert entry.get_text() == "ale"
    assert entry.focused is True


def test_curses_system_info_uses_uname(monkeypatch):
    monkeypatch.setattr(
        curses_greeter.os,
        "uname",
        lambda: types.SimpleNamespace(nodename="host", release="6.1.0", machine="x86_64"),
    )

    assert curses_greeter._system_info() == "host  6.1.0  x86_64"


def test_curses_greeter_loads_state_file(monkeypatch, tmp_path):
    state_file = tmp_path / "last-session"

    monkeypatch.setenv("WLDM_STATE_FILE", str(state_file))
    monkeypatch.setattr(curses_greeter.wldm.state, "load_last_session_file", lambda path: ("alice", "labwc"))
    monkeypatch.setattr(
        curses_greeter.wldm.sessions,
        "desktop_sessions",
        lambda username="": [
            {"name": "Labwc", "command": "labwc", "desktop_names": []},
        ],
    )

    app = curses_greeter.GreeterApp(FakeScreen(), client=DummyClient())

    assert app.state_file == str(state_file)
    assert app.last_username == "alice"
    assert app.username_entry is not None
    assert app.username_entry.get_text() == "alice"
    assert app.selected_session == 0


def test_curses_greeter_reads_secret_from_text_entry():
    app = curses_greeter.GreeterApp(FakeScreen(), client=DummyClient())
    entry = curses_greeter.TextEntry("secret")

    secret = app.read_password_secret(entry)

    assert secret.as_bytes() == b"secret"
    secret.clear()


def test_curses_greeter_selects_preferred_session(monkeypatch):
    monkeypatch.setattr(
        curses_greeter.wldm.sessions,
        "desktop_sessions",
        lambda username="": [
            {"name": "Sway", "command": "sway", "desktop_names": ["sway"]},
            {"name": "Labwc", "command": "labwc", "desktop_names": ["wlroots"]},
        ],
    )

    app = curses_greeter.GreeterApp(FakeScreen(), client=DummyClient())
    app.refresh_sessions("alice", preferred_command="labwc")

    assert app.selected_session == 1
    assert app.selected_session_data() == ("labwc", ["wlroots"], "Labwc", "", "")


def test_curses_greeter_moves_to_session_selection(monkeypatch):
    monkeypatch.setattr(
        curses_greeter.wldm.sessions,
        "desktop_sessions",
        lambda username="": [
            {"name": "Labwc", "command": "labwc", "desktop_names": []},
        ],
    )

    app = curses_greeter.GreeterApp(FakeScreen(), client=DummyClient())
    app.auth_username = "alice"
    app.set_session_ready()

    assert app.session_ready is True
    assert app.focus == "sessions"
    assert app.status_message == ""


def test_curses_greeter_handles_prompts_and_reset(monkeypatch):
    app = curses_greeter.GreeterApp(FakeScreen(), client=DummyClient())

    app.set_auth_state(True)
    assert app.auth_in_progress is True
    assert app.status_message == "Authenticating..."

    app.set_conversation_prompt("error", "Bad token")
    assert app.conversation_pending is True
    assert app.status_message == "Bad token"
    assert app.status_error is True

    app.set_conversation_prompt("secret", "Password:")
    assert app.focus == "password"
    assert app.status_message == ""

    app.auth_username = "alice"
    app.reset_auth_flow()
    assert app.focus == "username"
    assert app.session_ready is False
    assert app.username_entry is not None
    assert app.username_entry.get_text() == "alice"


def test_curses_greeter_saves_state(monkeypatch):
    calls = []
    app = curses_greeter.GreeterApp(FakeScreen(), client=DummyClient())
    app.state_file = "/tmp/wldm-state/last-session"
    app.last_username = "alice"
    app.last_session_command = "labwc"

    monkeypatch.setattr(
        curses_greeter.wldm.state,
        "save_last_session_file",
        lambda path, username, command: calls.append((path, username, command)),
    )

    app.save_last_session_state()

    assert calls == [("/tmp/wldm-state/last-session", "alice", "labwc")]


def test_curses_greeter_logs_state_save_errors(monkeypatch):
    warnings = []
    app = curses_greeter.GreeterApp(FakeScreen(), client=DummyClient())
    app.state_file = "/tmp/wldm-state/last-session"

    monkeypatch.setattr(
        curses_greeter.wldm.state,
        "save_last_session_file",
        lambda path, username, command: (_ for _ in ()).throw(OSError("denied")),
    )
    monkeypatch.setattr(curses_greeter.logger, "warning", lambda msg, *args: warnings.append(msg % args))

    app.save_last_session_state()

    assert "unable to save last-session state" in warnings[0]


def test_curses_greeter_handles_text_keys():
    app = curses_greeter.GreeterApp(FakeScreen(), client=DummyClient())

    app.handle_key(ord("a"))
    app.handle_key(ord("l"))
    app.handle_key(127)
    app.handle_key(ord("i"))
    app.handle_key(ord("q"))

    assert app.username_entry.get_text() == "aiq"
    assert app.quit is False


def test_curses_greeter_handles_utf8_text_keys():
    app = curses_greeter.GreeterApp(FakeScreen(), client=DummyClient())

    for char in "josé-алиса":
        app.handle_key(char)

    assert app.username_entry.get_text() == "josé-алиса"


def test_curses_greeter_reads_wide_characters():
    class WideScreen(FakeScreen):
        def get_wch(self):
            return "я"

    app = curses_greeter.GreeterApp(WideScreen(), client=DummyClient())

    assert app.read_key() == "я"


def test_curses_greeter_handles_control_keys(monkeypatch):
    app = curses_greeter.GreeterApp(FakeScreen(), client=DummyClient())
    calls = []

    monkeypatch.setattr(app, "on_login_clicked", lambda: calls.append("login"))
    monkeypatch.setattr(app, "on_cancel_clicked", lambda: calls.append("cancel"))

    app.handle_key(ord("\n"))
    app.handle_key(27)
    app.handle_key(ord("\t"))

    assert calls == ["login", "cancel"]
    assert app.focus == "password"


def test_curses_greeter_handles_session_keys(monkeypatch):
    monkeypatch.setattr(
        curses_greeter.wldm.sessions,
        "desktop_sessions",
        lambda username="": [
            {"name": "Sway", "command": "sway", "desktop_names": []},
            {"name": "Labwc", "command": "labwc", "desktop_names": []},
        ],
    )
    app = curses_greeter.GreeterApp(FakeScreen(), client=DummyClient())
    app.set_session_ready()

    app.handle_key(curses_greeter.curses.KEY_DOWN)
    assert app.selected_session == 1

    app.handle_key(curses_greeter.curses.KEY_UP)
    assert app.selected_session == 0

    app.sessions = []
    app.handle_key(curses_greeter.curses.KEY_DOWN)
    assert app.selected_session == 0


def test_curses_addstr_clips_and_ignores_out_of_bounds():
    screen = FakeScreen()

    curses_greeter._addstr(screen, 0, 79, "hidden")
    curses_greeter._addstr(screen, 30, 0, "hidden")
    curses_greeter._addstr(screen, 0, 0, "visible")

    assert screen.calls == [("addstr", (0, 0, "visible", 0))]


def test_curses_greeter_render_draws_prompt_and_sessions(monkeypatch):
    screen = FakeScreen()
    monkeypatch.setattr(curses_greeter, "_system_info", lambda: "host  6.1.0  x86_64")
    monkeypatch.setattr(curses_greeter.curses, "ACS_HLINE", 1001, raising=False)
    monkeypatch.setattr(curses_greeter.curses, "ACS_VLINE", 1002, raising=False)
    monkeypatch.setattr(curses_greeter.curses, "color_pair", lambda pair: pair * 100)
    monkeypatch.setattr(
        curses_greeter.wldm.sessions,
        "desktop_sessions",
        lambda username="": [
            {"name": "Labwc", "command": "labwc", "desktop_names": []},
        ],
    )
    app = curses_greeter.GreeterApp(screen, client=DummyClient())
    app.username_entry.set_text("alice")
    app.set_conversation_prompt("secret", "Password:")
    app.password_entry.set_text("secret")
    app.render()

    rendered = rendered_text(screen)
    assert "host  6.1.0  x86_64" in rendered
    assert "WLDM text greeter" in rendered
    assert rendered_contains(screen, "alice")
    assert rendered_contains(screen, "******")
    assert 1002 in rendered_chars(screen)
    assert 1001 in rendered_chars(screen)
    assert "[" in rendered
    assert "]" in rendered
    title_calls = [call for call in addstr_calls(screen) if call[2] == "WLDM text greeter"]
    assert title_calls[0][3] == curses_greeter.WINDOW_COLOR_PAIR * 100
    username_calls = [call for call in addstr_calls(screen) if call[2] == "Username:"]
    password_calls = [call for call in addstr_calls(screen) if call[2] == "Password:"]
    assert password_calls[0][0] == username_calls[0][0] + 1
    initial_username_y = username_calls[0][0]
    fill_calls = [call for call in addstr_calls(screen) if call[2].strip() == ""]
    assert fill_calls[0][3] == curses_greeter.WINDOW_COLOR_PAIR * 100

    screen.calls.clear()
    app.set_session_ready()
    app.render()
    rendered = rendered_text(screen)
    assert " Sessions " in rendered
    panel_title_calls = [call for call in addstr_calls(screen) if call[2] == " Sessions "]
    username_calls = [call for call in addstr_calls(screen) if call[2] == "Username:"]
    assert username_calls[0][0] == initial_username_y
    assert panel_title_calls[0][0] > username_calls[0][0] + 1
    session_calls = [call for call in addstr_calls(screen) if call[2].startswith("> Labwc")]
    assert session_calls
    assert session_calls[0][2] != "> Labwc"
    assert session_calls[0][2].endswith(" ")
    assert session_calls[0][3] & curses_greeter.curses.A_REVERSE
    assert session_calls[0][0] < screen.getmaxyx()[0] - 2


def test_curses_greeter_render_draws_empty_session_state(monkeypatch):
    screen = FakeScreen()
    app = curses_greeter.GreeterApp(screen, client=DummyClient())
    app.session_ready = True
    app.focus = "sessions"
    app.set_status("No session", error=True)
    monkeypatch.setattr(curses_greeter.curses, "color_pair", lambda pair: pair * 100)
    app.render()

    rendered = rendered_text(screen)
    assert rendered_contains(screen, "No sessions available")
    assert "No session" in rendered
    status_calls = [call for call in addstr_calls(screen) if call[2] == "No session"]
    assert status_calls
    assert status_calls[0][0] < screen.getmaxyx()[0] - 2
    assert status_calls[0][3] != 0


def test_curses_greeter_session_list_stays_inside_panel(monkeypatch):
    screen = FakeScreen()
    monkeypatch.setattr(curses_greeter.curses, "color_pair", lambda pair: pair * 100)
    monkeypatch.setattr(
        curses_greeter.wldm.sessions,
        "desktop_sessions",
        lambda username="": [
            {"name": f"Session {index}", "command": f"session-{index}", "desktop_names": []}
            for index in range(12)
        ],
    )
    app = curses_greeter.GreeterApp(screen, client=DummyClient())
    app.set_session_ready()

    app.render()

    session_calls = [call for call in addstr_calls(screen) if "Session " in call[2]]
    assert session_calls
    assert len(session_calls) == 6
    assert all(call[0] < screen.getmaxyx()[0] - 2 for call in session_calls)
    assert not any("Session 6" in call[2] for call in session_calls)


def test_curses_greeter_session_list_scrolls_to_selected_item(monkeypatch):
    screen = FakeScreen()
    monkeypatch.setattr(curses_greeter.curses, "color_pair", lambda pair: pair * 100)
    monkeypatch.setattr(
        curses_greeter.wldm.sessions,
        "desktop_sessions",
        lambda username="": [
            {"name": f"Session {index}", "command": f"session-{index}", "desktop_names": []}
            for index in range(12)
        ],
    )
    app = curses_greeter.GreeterApp(screen, client=DummyClient())
    app.set_session_ready()
    app.selected_session = 10

    app.render()

    session_calls = [call for call in addstr_calls(screen) if "Session " in call[2]]
    assert len(session_calls) == 6
    assert "Session 5" in session_calls[0][2]
    assert "Session 10" in session_calls[-1][2]
    assert session_calls[-1][2].startswith("> Session 10")
    assert session_calls[-1][3] & curses_greeter.curses.A_REVERSE


def test_curses_greeter_delegates_shared_helpers(monkeypatch):
    calls = []
    app = curses_greeter.GreeterApp(FakeScreen(), client=DummyClient())

    monkeypatch.setattr(curses_greeter.greeter_auth, "read_prompt_response", lambda obj: calls.append(("read", obj)) or None)
    monkeypatch.setattr(curses_greeter.greeter_client, "send_recv_answer", lambda obj, data, lock: calls.append(("send", data)) or {})
    monkeypatch.setattr(curses_greeter.greeter_auth, "start_selected_session", lambda obj, *args: calls.append(("start", args)) or True)
    monkeypatch.setattr(curses_greeter.greeter_auth, "handle_conversation_answer", lambda obj, answer: calls.append(("answer", answer)) or "ready")
    monkeypatch.setattr(curses_greeter.greeter_client, "handle_connection_lost", lambda obj: calls.append(("lost", obj)))
    monkeypatch.setattr(curses_greeter.greeter_client, "log_protocol_error", lambda obj, context, raw, error: calls.append(("log", context)))
    monkeypatch.setattr(curses_greeter.greeter_client, "poll_events", lambda obj, lock: calls.append(("poll", obj)))
    monkeypatch.setattr(curses_greeter.greeter_client, "handle_event", lambda obj, event: calls.append(("event", event)))
    monkeypatch.setattr(curses_greeter.greeter_auth, "on_cancel_clicked", lambda obj: calls.append(("cancel", obj)))
    monkeypatch.setattr(curses_greeter.greeter_auth, "on_login_clicked", lambda obj: calls.append(("login", obj)))
    monkeypatch.setattr(curses_greeter, "reexec_self", lambda client: calls.append(("reexec", client)))

    assert app.read_prompt_response() is None
    assert app.send_recv_answer({"action": "x"}) == {}
    assert app.start_selected_session("cmd", [], "", "", "") is True
    assert app.handle_conversation_answer({"ok": True}) == "ready"
    app.handle_connection_lost()
    app.log_protocol_error("bad", b"raw", RuntimeError("x"))
    app.poll_events()
    app.handle_event({"event": "x"})
    app.on_cancel_clicked()
    app.on_login_clicked()
    app.reexec_self()

    assert [call[0] for call in calls] == [
        "read", "send", "start", "answer", "lost", "log",
        "poll", "event", "cancel", "login", "reexec",
    ]


def test_curses_restore_terminal_resets_modes(monkeypatch):
    screen = FakeScreen()
    calls = []

    monkeypatch.setattr(curses_greeter.curses, "echo", lambda: calls.append("echo"))
    monkeypatch.setattr(curses_greeter.curses, "nocbreak", lambda: calls.append("nocbreak"))
    monkeypatch.setattr(curses_greeter.curses, "endwin", lambda: calls.append("endwin"))

    curses_greeter.restore_terminal(screen)

    assert ("nodelay", False) in screen.calls
    assert ("keypad", False) in screen.calls
    assert calls == ["echo", "nocbreak", "endwin"]


def test_curses_init_colors_uses_default_background(monkeypatch):
    calls = []

    monkeypatch.setattr(curses_greeter.curses, "start_color", lambda: calls.append(("start",)))
    monkeypatch.setattr(curses_greeter.curses, "use_default_colors", lambda: calls.append(("default",)))
    monkeypatch.setattr(curses_greeter.curses, "init_pair", lambda pair, fg, bg: calls.append(("pair", pair, fg, bg)))
    monkeypatch.setattr(curses_greeter.curses, "COLOR_WHITE", 7, raising=False)
    monkeypatch.setattr(curses_greeter.curses, "COLOR_BLUE", 4, raising=False)
    monkeypatch.setattr(curses_greeter.curses, "COLOR_RED", 1, raising=False)

    curses_greeter.init_colors()

    assert calls == [
        ("start",),
        ("default",),
        ("pair", curses_greeter.WINDOW_COLOR_PAIR, 7, 4),
        ("pair", curses_greeter.ERROR_COLOR_PAIR, 1, 4),
    ]


def test_curses_greeter_run_loop_quits(monkeypatch):
    screen = FakeScreen()
    calls = []

    monkeypatch.setattr(curses_greeter.curses, "curs_set", lambda value: calls.append(("cursor", value)))
    monkeypatch.setattr(curses_greeter.curses, "napms", lambda value: None)
    monkeypatch.setattr(curses_greeter, "init_colors", lambda: calls.append("colors"))

    app = curses_greeter.GreeterApp(screen, client=DummyClient())
    monkeypatch.setattr(app, "poll_events", lambda: setattr(app, "quit", True))

    assert app.run() == wldm.EX_SUCCESS
    assert app.quit is True
    assert calls == [("cursor", 0), "colors"]
    assert ("nodelay", True) in screen.calls
    assert ("keypad", True) in screen.calls


def test_curses_greeter_reexec_restores_terminal(monkeypatch):
    screen = FakeScreen()
    calls = []
    app = curses_greeter.GreeterApp(screen, client=DummyClient())

    monkeypatch.setattr(curses_greeter, "restore_terminal", lambda target: calls.append(("restore", target)))
    monkeypatch.setattr(curses_greeter, "reexec_self", lambda client: calls.append(("reexec", client)))

    app.reexec_self()

    assert calls == [("restore", screen), ("reexec", app.client)]


def test_curses_reexec_preserves_socket_fd_and_original_argv(monkeypatch):
    calls = {}

    class FakeSocket:
        @staticmethod
        def fileno():
            return 12

    monkeypatch.setattr(curses_greeter.sys, "orig_argv", ["/usr/bin/python3", "-I", "-P", "wldm.command"], raising=False)
    monkeypatch.setattr(curses_greeter.os, "set_inheritable", lambda fd, value: calls.update({"fd": (fd, value)}))
    monkeypatch.setattr(
        curses_greeter.os,
        "execvpe",
        lambda prog, argv, env: calls.update({"exec": (prog, argv, env)}),
    )

    curses_greeter.reexec_self(types.SimpleNamespace(sock=FakeSocket()))

    assert calls["fd"] == (12, True)
    assert calls["exec"][1] == ["/usr/bin/python3", "-I", "-P", "wldm.command"]
    assert calls["exec"][2] is curses_greeter.os.environ


def test_curses_reexec_rebuilds_argv_without_orig_argv(monkeypatch):
    calls = {}

    monkeypatch.setattr(curses_greeter.sys, "orig_argv", None, raising=False)
    monkeypatch.setattr(curses_greeter.sys, "executable", "/usr/bin/python3")
    monkeypatch.setattr(curses_greeter.sys, "argv", ["-m", "wldm.command"])
    monkeypatch.setattr(curses_greeter.sys, "flags", types.SimpleNamespace(isolated=1, safe_path=True))
    monkeypatch.setattr(
        curses_greeter.os,
        "execvpe",
        lambda prog, argv, env: calls.update({"exec": (prog, argv, env)}),
    )

    curses_greeter.reexec_self(types.SimpleNamespace())

    assert calls["exec"][1] == ["/usr/bin/python3", "-I", "-P", "-m", "wldm.command"]


def test_curses_cmd_main_uses_wrapper(monkeypatch):
    calls = []

    monkeypatch.setattr(curses_greeter.curses, "wrapper", lambda callback: calls.append(callback) or wldm.EX_SUCCESS)

    assert curses_greeter.cmd_main() == wldm.EX_SUCCESS
    assert calls == [curses_greeter.run_wrapped]
