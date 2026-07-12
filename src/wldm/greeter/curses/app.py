#!/usr/bin/env python
# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026  Alexey Gladkov <legion@kernel.org>

import curses
import os
import signal
import sys
import threading
from typing import Any, Dict

import wldm
import wldm.greeter.auth as greeter_auth
import wldm.greeter.client as greeter_client
import wldm.greeter.contracts as greeter_contracts
import wldm.ipc_client
import wldm.secret
import wldm.sessions
import wldm.state

logger = wldm.logger
lock = threading.Lock()

KEY_BACKSPACE = {8, 127, curses.KEY_BACKSPACE}
STR_BACKSPACE = {"\b", "\x7f"}
WINDOW_COLOR_PAIR = 1
ERROR_COLOR_PAIR = 2
_active_screen: Any | None = None


class TextEntry:
    """Small editable text model used by the curses greeter."""

    def __init__(self, text: str = "") -> None:
        self.text = text
        self.focused = False

    def get_text(self) -> str:
        """Return the current entry text."""
        return self.text

    def set_text(self, text: str) -> None:
        """Replace the current entry text."""
        self.text = text

    def grab_focus(self) -> None:
        """Mark this entry as focused."""
        self.focused = True

    def append(self, char: str) -> None:
        """Append one printable character."""
        self.text += char

    def backspace(self) -> None:
        """Remove the final character."""
        self.text = self.text[:-1]


def _configured_state_file() -> str:
    return os.environ.get("WLDM_STATE_FILE", "").strip()


def _system_info() -> str:
    info = os.uname()
    return f"{info.nodename}  {info.release}  {info.machine}"


def _reexec_argv() -> list[str]:
    orig_argv = getattr(sys, "orig_argv", None)
    if isinstance(orig_argv, list) and orig_argv:
        return [str(item) for item in orig_argv]

    argv = [sys.executable]

    if getattr(sys.flags, "isolated", 0):
        argv.append("-I")

    if getattr(sys.flags, "safe_path", False):
        argv.append("-P")

    argv.extend(sys.argv)
    return argv


def reexec_self(client: Any) -> None:
    """Replace this greeter process while preserving the daemon IPC socket."""
    sock = getattr(client, "sock", None)
    if sock is not None and hasattr(sock, "fileno"):
        os.set_inheritable(sock.fileno(), True)

    argv = _reexec_argv()
    os.execvpe(argv[0], argv, os.environ)


def _addstr(screen: Any, y: int, x: int, text: str, attr: int = 0) -> None:
    height, width = screen.getmaxyx()
    if y < 0 or y >= height or x >= width:
        return

    clipped = text[:max(0, width - x - 1)]
    if clipped:
        screen.addstr(y, x, clipped, attr)


def _addch(screen: Any, y: int, x: int, char: int, attr: int = 0) -> None:
    height, width = screen.getmaxyx()
    if y < 0 or y >= height or x >= width:
        return

    screen.addch(y, x, char, attr)


def _acs(name: str, fallback: str) -> int:
    value = getattr(curses, name, None)
    if isinstance(value, int):
        return value
    return ord(fallback)


def _fill_rect(screen: Any, y: int, x: int, height: int, width: int, attr: int) -> None:
    if height <= 0 or width <= 0:
        return

    line = " " * width
    for row in range(height):
        _addstr(screen, y + row, x, line, attr)


def _draw_box(screen: Any, y: int, x: int, height: int, width: int, attr: int = 0) -> None:
    if height < 3 or width < 4:
        return

    hline = _acs("ACS_HLINE", "-")
    vline = _acs("ACS_VLINE", "|")
    _addch(screen, y, x, _acs("ACS_ULCORNER", "+"), attr)
    _addch(screen, y, x + width - 1, _acs("ACS_URCORNER", "+"), attr)
    _addch(screen, y + height - 1, x, _acs("ACS_LLCORNER", "+"), attr)
    _addch(screen, y + height - 1, x + width - 1, _acs("ACS_LRCORNER", "+"), attr)

    for column in range(1, width - 1):
        _addch(screen, y, x + column, hline, attr)
        _addch(screen, y + height - 1, x + column, hline, attr)

    for row in range(1, height - 1):
        _addch(screen, y + row, x, vline, attr)
        _addch(screen, y + row, x + width - 1, vline, attr)


def _draw_field(screen: Any,
                y: int,
                x: int,
                width: int,
                value: str,
                focused: bool,
                secret: bool = False,
                attr: int = 0) -> None:
    if width < 4:
        return

    visible = "*" * len(value) if secret else value
    inner_width = width - 2
    if len(visible) > inner_width:
        visible = visible[-inner_width:]

    field_attr = attr | (curses.A_REVERSE if focused else 0)
    _addstr(screen, y, x, "[", attr)
    _addstr(screen, y, x + 1, visible.ljust(inner_width), field_attr)
    _addstr(screen, y, x + width - 1, "]", attr)


def _draw_list_item(screen: Any, y: int, x: int, width: int, text: str, attr: int) -> None:
    if width <= 0:
        return

    _addstr(screen, y, x, text[:width].ljust(width), attr)


def _scroll_offset(selected: int, total: int, visible: int) -> int:
    if total <= visible or visible <= 0:
        return 0

    return min(max(0, selected - visible + 1), total - visible)


def _window_attr() -> int:
    try:
        return curses.color_pair(WINDOW_COLOR_PAIR)
    except curses.error:
        return 0


def _status_attr(error: bool) -> int:
    if not error:
        return _window_attr()

    try:
        return curses.color_pair(ERROR_COLOR_PAIR) | curses.A_BOLD
    except curses.error:
        return curses.A_BOLD


def restore_terminal(screen: Any | None = None) -> None:
    """Restore terminal modes before leaving the curses frontend."""
    if screen is not None:
        for method, value in (("nodelay", False), ("keypad", False)):
            try:
                getattr(screen, method)(value)
            except (AttributeError, curses.error):
                pass

    try:
        curses.echo()
    except curses.error:
        pass

    try:
        curses.nocbreak()
    except curses.error:
        pass

    try:
        curses.endwin()
    except curses.error:
        pass


def _restore_and_exit(signum: int, _frame: Any) -> None:
    restore_terminal(_active_screen)
    raise SystemExit(128 + signum)


def init_colors() -> None:
    """Use the terminal default foreground and background when available."""
    try:
        curses.start_color()
        curses.use_default_colors()
        curses.init_pair(WINDOW_COLOR_PAIR, curses.COLOR_WHITE, curses.COLOR_BLUE)
        curses.init_pair(ERROR_COLOR_PAIR, curses.COLOR_RED, curses.COLOR_BLUE)
    except curses.error:
        pass


class GreeterApp:
    """Curses implementation of the greeter frontend contract."""

    def __init__(self, screen: Any, client: Any | None = None) -> None:
        self.screen = screen
        self.client = client if client is not None else wldm.ipc_client.SocketClient.from_inherited_env()
        self._username_entry = TextEntry()
        self._password_entry = TextEntry()
        self.username_entry: greeter_contracts.GreeterEntry | None = self._username_entry
        self.password_entry: greeter_contracts.GreeterEntry | None = self._password_entry
        self.status_message = ""
        self.status_error = False
        self.auth_in_progress = False
        self.conversation_pending = False
        self.conversation_prompt_style = ""
        self.conversation_prompt_text = ""
        self.session_ready = False
        self.auth_username = ""
        self.last_username = ""
        self.last_session_command = ""
        self.sessions: list[dict[str, Any]] = []
        self.selected_session = 0
        self.state_file = _configured_state_file()
        self.focus = "username"
        self.quit = False

        if self.state_file:
            self.last_username, self.last_session_command = wldm.state.load_last_session_file(self.state_file)
            self._username_entry.set_text(self.last_username)

        self.refresh_sessions(self.last_username, preferred_command=self.last_session_command)
        self._set_focus("username")

    def _set_focus(self, focus: str) -> None:
        self.focus = focus
        self._username_entry.focused = focus == "username"
        self._password_entry.focused = focus == "password"

    def set_status(self, message: str, error: bool = False) -> None:
        """Show one status message."""
        self.status_message = message
        self.status_error = error

    def set_auth_state(self, busy: bool) -> None:
        """Switch into or out of a busy authentication state."""
        self.auth_in_progress = busy
        if busy:
            self.set_status("Authenticating...")

    def clear_conversation_state(self) -> None:
        """Forget the current authentication conversation state."""
        self.conversation_pending = False
        self.conversation_prompt_style = ""
        self.conversation_prompt_text = ""
        self.session_ready = False
        self.auth_username = ""

    def clear_username_selection(self) -> None:
        """Keep the text cursor at the end of the username field."""

    def set_conversation_prompt(self, style: str, text: str) -> None:
        """Show one pending authentication prompt."""
        self.conversation_pending = True
        self.session_ready = False
        self.conversation_prompt_style = style
        self.conversation_prompt_text = text
        self._password_entry.set_text("")

        if style in {"info", "error"} and text:
            self.set_status(text, error=style == "error")
            return

        self.set_status("")
        self._set_focus("password")

    def set_session_ready(self) -> None:
        """Move to the post-auth session selection stage."""
        self.conversation_pending = False
        self.conversation_prompt_style = ""
        self.conversation_prompt_text = ""
        self.session_ready = True
        self.refresh_sessions(self.auth_username, preferred_command=self.last_session_command)
        self.set_status("")
        self._set_focus("sessions")

    def reset_auth_flow(self) -> None:
        """Return to the initial username entry stage."""
        username = self.auth_username.strip()
        self.set_auth_state(False)
        self.clear_conversation_state()
        self._password_entry.set_text("")
        self._username_entry.set_text(username)
        self.refresh_sessions(username, preferred_command=self.last_session_command)
        self.set_status("")
        self._set_focus("username")

    def refresh_sessions(self, username: str = "", preferred_command: str = "") -> None:
        """Refresh the available session list."""
        self.sessions = wldm.sessions.desktop_sessions(username)
        self.selected_session = 0

        if not self.sessions:
            return

        command = preferred_command or self.last_session_command
        for index, session in enumerate(self.sessions):
            if command and session["command"] == command:
                self.selected_session = index
                break

    def save_last_session_state(self) -> None:
        """Persist the remembered greeter state."""
        if not self.state_file:
            return

        try:
            wldm.state.save_last_session_file(self.state_file, self.last_username, self.last_session_command)
        except OSError as e:
            logger.warning("unable to save last-session state in %s: %s", self.state_file, e)

    def selected_session_data(self) -> tuple[str, list[str], str, str, str]:
        """Return the selected session command and desktop metadata."""
        if not self.sessions:
            return "", [], "", "", ""

        entry = self.sessions[self.selected_session]
        return (
            str(entry.get("command", "")),
            list(entry.get("desktop_names", [])),
            str(entry.get("name", "")),
            str(entry.get("icon", "")),
            str(entry.get("desktop_file", "")),
        )

    def read_password_secret(self, entry: greeter_contracts.GreeterEntry) -> wldm.secret.SecretBytes:
        """Read a curses password entry into erasable secret storage."""
        return wldm.secret.SecretBytes(entry.get_text())

    def read_prompt_response(self) -> wldm.secret.SecretBytes | None:
        """Read one answer for the current pending auth prompt."""
        return greeter_auth.read_prompt_response(self)

    def send_recv_answer(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Send one daemon request and return the matching response."""
        return greeter_client.send_recv_answer(self, data, lock)

    def start_selected_session(self,
                               command: str,
                               desktop_names: list[str],
                               name: str = "",
                               icon: str = "",
                               desktop_file: str = "") -> bool:
        """Ask the daemon to start one authenticated user session."""
        return greeter_auth.start_selected_session(self, command, desktop_names, name, icon, desktop_file)

    def handle_conversation_answer(self, answer: Dict[str, Any]) -> str:
        """Advance the current greeter-side conversation state from one reply."""
        return greeter_auth.handle_conversation_answer(self, answer)

    def handle_connection_lost(self) -> None:
        """Handle loss of the daemon IPC channel."""
        greeter_client.handle_connection_lost(self)

    def log_protocol_error(self, context: str, raw: bytes, error: Exception) -> None:
        """Log one malformed daemon protocol message."""
        greeter_client.log_protocol_error(self, context, raw, error)

    def poll_events(self) -> None:
        """Poll pending daemon events."""
        greeter_client.poll_events(self, lock)

    def handle_event(self, event: Dict[str, Any]) -> None:
        """Handle one asynchronous daemon event."""
        greeter_client.handle_event(self, event)

    def reexec_self(self) -> None:
        """Replace the current process image."""
        restore_terminal(self.screen)
        reexec_self(self.client)

    def on_quit(self) -> None:
        """Request frontend shutdown."""
        self.quit = True
        self.client.close()

    def on_cancel_clicked(self) -> None:
        """Cancel the current auth flow."""
        greeter_auth.on_cancel_clicked(self)

    def on_login_clicked(self) -> None:
        """Advance the current login flow."""
        greeter_auth.on_login_clicked(self)

    def handle_key(self, key: int | str) -> None:
        """Apply one keyboard event."""
        if key in ("\n", "\r", ord("\n"), ord("\r"), curses.KEY_ENTER):
            self.on_login_clicked()
            return

        if key in ("\x1b", 27):
            self.on_cancel_clicked()
            return

        if key in ("\t", ord("\t")):
            self._cycle_focus()
            return

        if self.focus == "sessions":
            if isinstance(key, int):
                self._handle_session_key(key)
            return

        entry = self._username_entry if self.focus == "username" else self._password_entry
        if key in KEY_BACKSPACE or key in STR_BACKSPACE:
            entry.backspace()
            return

        if isinstance(key, str):
            if len(key) == 1 and key.isprintable():
                entry.append(key)
            return

        if 32 <= key <= 126:
            entry.append(chr(key))

    def _cycle_focus(self) -> None:
        if self.session_ready:
            self._set_focus("sessions")
            return

        if self.conversation_pending and self.conversation_prompt_style in {"secret", "visible"}:
            self._set_focus("password")
            return

        self._set_focus("password" if self.focus == "username" else "username")

    def _handle_session_key(self, key: int) -> None:
        if not self.sessions:
            return

        if key == curses.KEY_UP:
            self.selected_session = max(0, self.selected_session - 1)
            return

        if key == curses.KEY_DOWN:
            self.selected_session = min(len(self.sessions) - 1, self.selected_session + 1)

    def render(self) -> None:
        """Draw the current greeter state."""
        self.screen.erase()

        height, width = self.screen.getmaxyx()
        attr_focus = curses.A_REVERSE
        box_width = min(62, max(32, width - 4))
        max_box_height = max(8, height - 4)
        expanded_box_height = min(18, max_box_height)
        box_height = expanded_box_height if self.session_ready else min(12, max_box_height)
        box_y = max(0, (height - expanded_box_height) // 3)
        box_x = max(0, (width - box_width) // 2)
        body_x = box_x + 3
        field_x = box_x + 16
        field_width = max(12, box_width - 20)
        username_y = box_y + 3
        prompt_y = username_y + 1
        status_y = box_y + box_height - 2
        window_attr = _window_attr()

        _addstr(self.screen, 0, 0, _system_info())
        _fill_rect(self.screen, box_y + 1, box_x + 1, box_height - 2, box_width - 2, window_attr)
        _draw_box(self.screen, box_y, box_x, box_height, box_width, window_attr)
        _addstr(self.screen, box_y + 1, body_x, "WLDM text greeter", window_attr)
        _addstr(self.screen, username_y, body_x, "Username:", window_attr)
        _draw_field(
            self.screen,
            username_y,
            field_x,
            field_width,
            self._username_entry.get_text(),
            self.focus == "username",
            attr=window_attr,
        )

        if self.conversation_pending:
            prompt = self.conversation_prompt_text or "Response:"
            _addstr(self.screen, prompt_y, body_x, prompt, window_attr)
            if self.conversation_prompt_style in {"secret", "visible"}:
                _draw_field(
                    self.screen,
                    prompt_y,
                    field_x,
                    field_width,
                    self._password_entry.get_text(),
                    self.focus == "password",
                    secret=self.conversation_prompt_style == "secret",
                    attr=window_attr,
                )

        if self.session_ready:
            panel_y = prompt_y + 2
            panel_x = box_x + 2
            panel_height = max(4, status_y - panel_y - 1)
            panel_width = box_width - 4
            list_x = panel_x + 2
            list_y = panel_y + 2
            list_width = max(1, panel_width - 4)
            visible_sessions = max(1, panel_height - 3)

            _fill_rect(self.screen, panel_y + 1, panel_x + 1, panel_height - 2, panel_width - 2, window_attr)
            _draw_box(self.screen, panel_y, panel_x, panel_height, panel_width, window_attr)
            _addstr(self.screen, panel_y, panel_x + 2, " Sessions ", window_attr)

            if not self.sessions:
                _draw_list_item(self.screen, list_y, list_x, list_width, "No sessions available", window_attr)
            first_session = _scroll_offset(self.selected_session, len(self.sessions), visible_sessions)
            visible_entries = self.sessions[first_session:first_session + visible_sessions]
            for offset, session in enumerate(visible_entries):
                index = first_session + offset
                marker = ">" if index == self.selected_session else " "
                attr = window_attr | (attr_focus if self.focus == "sessions" and index == self.selected_session else 0)
                _draw_list_item(self.screen, list_y + offset, list_x, list_width, f"{marker} {session['name']}", attr)

        status_attr = _status_attr(self.status_error)
        _addstr(self.screen, status_y, body_x, self.status_message, status_attr)
        _addstr(self.screen, height - 1, 0, "Enter: continue/start  Tab: switch  Esc: cancel")
        self.screen.refresh()

    def run(self) -> int:
        """Run the curses greeter event loop."""
        self.screen.nodelay(True)
        self.screen.keypad(True)

        try:
            curses.curs_set(0)
        except curses.error:
            pass

        init_colors()

        while not self.quit:
            self.poll_events()
            self.render()
            key = self.read_key()
            if key is not None:
                self.handle_key(key)
            curses.napms(50)

        return wldm.EX_SUCCESS

    def read_key(self) -> int | str | None:
        """Read one keyboard event, preserving wide characters when possible."""
        get_wch = getattr(self.screen, "get_wch", None)
        if get_wch is not None:
            try:
                key = get_wch()
            except curses.error:
                return None
            return key if key != -1 else None

        key = self.screen.getch()
        return key if key != -1 else None


def run_wrapped(screen: Any) -> int:
    global _active_screen  # pylint: disable=global-statement

    _active_screen = screen
    try:
        return GreeterApp(screen).run()
    finally:
        _active_screen = None


def cmd_main() -> int:
    """Run the curses greeter backend."""
    previous_handlers = {
        signum: signal.getsignal(signum)
        for signum in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM)
    }

    try:
        for signum in previous_handlers:
            signal.signal(signum, _restore_and_exit)

        ret: int = curses.wrapper(run_wrapped)
        return ret
    finally:
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)
