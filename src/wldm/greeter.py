#!/usr/bin/env python
# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026  Alexey Gladkov <legion@kernel.org>

import argparse
import dataclasses
import gettext
import locale
import os
import os.path
import select
import socket
import sys
import threading
import time
import traceback

from typing import Optional, Dict, Any

import gi  # type: ignore[import-untyped]
gi.require_version("Gtk", "4.0")

# pylint: disable=too-many-lines
# pylint: disable-next=wrong-import-position
from gi.repository import Gtk, Gdk, Gio, GLib  # type: ignore[import-untyped]

# pylint: disable-next=wrong-import-position
import wldm
# pylint: disable-next=wrong-import-position
from wldm import _gtk_ffi as gtk_ffi
# pylint: disable-next=wrong-import-position
import wldm.inifile
# pylint: disable-next=wrong-import-position
import wldm.policy
# pylint: disable-next=wrong-import-position
import wldm.protocol
# pylint: disable-next=wrong-import-position
import wldm.sessions
# pylint: disable-next=wrong-import-position
import wldm.state

logger = wldm.logger
resource_path: str
lock = threading.Lock()
GETTEXT_DOMAIN = "wldm"
_ = gettext.gettext

def is_valid_widget(spec: Dict[str, Any], widget: Any) -> bool:
    if spec.get("editable", False):
        editable_iface = getattr(Gtk, "Editable", None)

        if editable_iface is not None and not isinstance(widget, editable_iface):
            return False

    methods = tuple(spec.get("methods", ()))

    return all(hasattr(widget, method) for method in methods)


def account_service_profile(username: str) -> Dict[str, str] | None:
    if not username:
        return None

    path = os.path.join(wldm.policy.ACCOUNTS_SERVICE_USERS_DIR, username)
    try:
        data = wldm.inifile.read_ini_file(
            path,
            allowed={"User": {"RealName", "Icon"}},
            max_size=wldm.policy.ACCOUNT_SERVICE_MAX_FILE_SIZE,
            ignore_unknown_sections=True,
            ignore_unknown_keys=True,
        )
    except OverflowError:
        logger.warning("ignoring oversized AccountsService profile: %s", path)
        return None
    except (OSError, RuntimeError, UnicodeError, ValueError) as e:
        logger.debug("unable to read AccountsService profile %s: %s", path, e)
        return None

    display_name = data.get("User", "RealName", default="").strip()
    avatar_path = data.get("User", "Icon").strip()

    if not display_name and not avatar_path:
        return None

    if avatar_path and not os.path.isfile(avatar_path):
        avatar_path = ""

    return {
        "display_name": display_name or username,
        "avatar_path": avatar_path,
    }

class SocketClient:
    def __init__(self, fd: int) -> None:
        self.sock = socket.socket(fileno=fd)

    def write_message(self, message: Dict[str, Any]) -> None:
        self.sock.sendall(wldm.protocol.encode_message(message))

    def read_message(self) -> Dict[str, Any] | None:
        return wldm.protocol.read_message_socket(self.sock)

    def can_read(self) -> bool:
        readable, _, _ = select.select([self.sock], [], [], 0.0)
        return self.sock in readable

    def close(self) -> None:
        self.sock.close()


def new_ipc_client() -> Any:
    socket_fd = os.environ.get("WLDM_SOCKET_FD", "").strip()
    if socket_fd:
        return SocketClient(fd=int(socket_fd))

    raise RuntimeError("environ variable `WLDM_SOCKET_FD' not specified")


def available_actions() -> set[str]:
    value = os.environ.get("WLDM_ACTIONS", "")
    return {item for item in value.split(":") if item}


@dataclasses.dataclass(frozen=True)
class KeyboardLayout:
    short_name: str
    long_name: str


def configured_keyboard_short_names() -> list[str]:
    value = os.environ.get("XKB_DEFAULT_LAYOUT", "").strip()
    return [item.strip() for item in value.split(",") if item.strip()]


def configured_state_file() -> str:
    return os.environ.get("WLDM_STATE_FILE", "").strip()


def clear_entry_selection(entry: Any) -> None:
    if hasattr(entry, "select_region"):
        text = ""

        if hasattr(entry, "get_text"):
            text = str(entry.get_text())

        entry.select_region(len(text), len(text))
        return

    if hasattr(entry, "set_position"):
        entry.set_position(-1)


def keyboard_state() -> tuple[list[KeyboardLayout], int]:
    display = Gdk.Display.get_default()

    if display is None or not hasattr(display, "get_default_seat"):
        return [], -1

    seat = display.get_default_seat()
    if seat is None or not hasattr(seat, "get_keyboard"):
        return [], -1

    keyboard = seat.get_keyboard()
    if keyboard is None:
        return [], -1

    if not hasattr(keyboard, "get_layout_names") or not hasattr(keyboard, "get_active_layout_index"):
        return [], -1

    try:
        layout_names = keyboard.get_layout_names()
        active_index = keyboard.get_active_layout_index()

    except Exception as e:
        logger.debug("unable to read keyboard layout state: %s", e)
        return [], -1

    if not layout_names or not isinstance(active_index, int):
        return [], -1

    if active_index < 0 or active_index >= len(layout_names):
        return [], -1

    configured_names = configured_keyboard_short_names()
    layouts = []

    for index, name in enumerate(layout_names):
        long_name = str(name).strip()

        if not long_name:
            continue

        short_name = configured_names[index] if index < len(configured_names) else long_name
        layouts.append(KeyboardLayout(short_name=short_name, long_name=long_name))

    if active_index >= len(layouts):
        return [], -1

    return layouts, active_index


def setup_greeter_logging() -> None:
    def log_uncaught_exception(exc_type: type[BaseException],
                               exc_value: BaseException,
                               exc_traceback: Any) -> None:
        logger.critical(
            "uncaught greeter exception:\n%s",
            "".join(traceback.format_exception(exc_type, exc_value, exc_traceback)).rstrip(),
        )

    sys.excepthook = log_uncaught_exception


def greeter_locale_path() -> str:
    if "WLDM_LOCALE_DIR" in os.environ:
        return os.path.abspath(os.environ["WLDM_LOCALE_DIR"])

    theme_locale = os.path.join(resource_path, "locale")

    if os.path.isdir(theme_locale):
        return theme_locale

    return ""


def setup_greeter_i18n() -> None:
    try:
        locale.setlocale(locale.LC_ALL, "")

    except locale.Error as e:
        logger.debug("unable to set process locale from environment: %s", e)

    gettext.bindtextdomain(GETTEXT_DOMAIN, greeter_locale_path())
    gettext.textdomain(GETTEXT_DOMAIN)


def default_resource_path() -> str:
    data_dir = os.environ.get("WLDM_DATA_DIR", "").strip()

    if not data_dir:
        return ""

    return os.path.join(os.path.abspath(data_dir), "resources")


def greeter_theme() -> str:
    return os.environ.get("WLDM_THEME", "default").strip() or "default"


def themed_resource_path() -> str:
    base = default_resource_path()
    theme = greeter_theme()

    if theme == "default":
        return base

    themed = os.path.join(os.path.dirname(base), "themes", theme)

    if os.path.isdir(themed):
        return themed

    logger.warning("theme '%s' not found, falling back to default", theme)
    return base


def load_builder_from_resource_path() -> Any:
    """Create a GtkBuilder loaded from the current greeter resource path."""
    builder = Gtk.Builder.new()
    builder.set_translation_domain(GETTEXT_DOMAIN)
    builder.add_from_file(os.path.join(resource_path, "greeter.ui"))
    return builder


class LoginApp:
    def __init__(self, client: Optional[Any]=None) -> None:
        self.app = Gtk.Application(application_id=wldm.policy.GREETER_APP_ID,
                                   flags=Gio.ApplicationFlags.FLAGS_NONE)

        self.app.connect('activate', self.on_activate)

        self.username_entry: Optional[Any] = None
        self.password_entry: Optional[Any] = None
        self.status_label:   Optional[Any] = None
        self.sessions_entry: Optional[Any] = None
        self.login_button:   Optional[Any] = None
        self.quit_button:    Optional[Any] = None
        self.reboot_button:  Optional[Any] = None
        self.suspend_button: Optional[Any] = None
        self.hibernate_button: Optional[Any] = None
        self.hostname_label: Optional[Any] = None
        self.date_label:     Optional[Any] = None
        self.time_label:     Optional[Any] = None
        self.keyboard_label: Optional[Any] = None
        self.session_label:  Optional[Any] = None
        self.identity_preview: Optional[Any] = None
        self.identity_label: Optional[Any] = None
        self.avatar_label:   Optional[Any] = None

        self.sessions = wldm.sessions.desktop_sessions()
        self.client = client if client is not None else new_ipc_client()

        self.quit = False
        self.auth_in_progress = False
        self.conversation_pending = False
        self.conversation_prompt_style = ""
        self.conversation_prompt_text = ""
        self.session_ready = False
        self.actions = available_actions()
        self.state_file = configured_state_file()
        self.last_username = ""
        self.last_session_command = ""
        self.auth_username = ""

        if self.state_file:
            self.last_username, self.last_session_command = wldm.state.load_last_session_file(self.state_file)

        self.WIDGET_BINDINGS: list[Dict[str, Any]] = [
            {
                "name": "main_window",
                "required": True,
                "methods": ("set_application", "set_default_widget", "present")},
            {
                "name": "username_entry",
                "required": True,
                "methods": ("get_text", "set_text", "connect", "grab_focus"),
                "editable": True,
                "signals": (
                    ("changed", self.on_username_changed),
                    ("activate", self.on_username_activate),
                ),
            },
            {
                "name": "password_entry",
                "required": True,
                "methods": ("get_text", "set_text", "connect", "grab_focus"),
                "editable": True,
                "signals": (("activate", self.on_login_clicked),),
            },
            {
                "name": "sessions_entry",
                "methods": ("connect", "set_model", "set_selected", "get_selected_item"),
                "signals": (
                    ("notify::selected-item", self.on_session_changed),
                    ("activate", self.on_login_clicked),
                ),
            },
            {
                "name": "status_label",
                "methods": ("set_text",)},
            {
                "name": "login_button",
                "required": True,
                "methods": ("connect", "set_sensitive"),
                "signals": (("clicked", self.on_login_clicked),),
            },
            {
                "name": "quit_button",
                "methods": ("connect", "set_visible"),
                "signals": (("clicked", self.on_poweroff_clicked),),
            },
            {
                "name": "reboot_button",
                "methods": ("connect", "set_visible"),
                "signals": (("clicked", self.on_reboot_clicked),),
            },
            {
                "name": "suspend_button",
                "methods": ("connect", "set_visible"),
                "signals": (("clicked", self.on_suspend_clicked),),
            },
            {
                "name": "hibernate_button",
                "methods": ("connect", "set_visible"),
                "signals": (("clicked", self.on_hibernate_clicked),),
            },
            {
                "name": "hostname_label",
                "methods": ("set_text",)
            },
            {
                "name": "date_label",
                "methods": ("set_text",)
            },
            {
                "name": "time_label",
                "methods": ("set_text",)
            },
            {
                "name": "keyboard_label",
                "methods": ("set_text", "set_visible", "set_tooltip_text", "set_width_chars")
            },
            {
                "name": "session_label",
                "methods": ("set_text",)
            },
            {
                "name": "identity_preview",
                "methods": ("set_visible",)
            },
            {
                "name": "identity_label",
                "methods": ("set_text",)
            },
            {
                "name": "avatar_label",
                "methods": ("set_text",)
            },
        ]

    def collect_theme_widgets(self, builder: Any) -> list[tuple[Dict[str, Any], Any]]:
        bindings: list[tuple[Dict[str, Any], Any]] = []
        invalid = []

        for spec in self.WIDGET_BINDINGS:
            name = str(spec["name"])

            try:
                widget = builder.get_object(name)
            except Exception:
                widget = None

            if widget is None:
                if spec.get("required", False):
                    invalid.append(name)
                continue

            if not is_valid_widget(spec, widget):
                invalid.append(name)
                continue

            bindings.append((spec, widget))

        if invalid:
            raise RuntimeError(f"theme '{greeter_theme()}' is missing or invalid for required widget(s): {', '.join(invalid)}")

        return bindings

    def set_status(self, message: str, error: bool = False) -> None:
        if self.status_label is not None:
            if hasattr(self.status_label, "remove_css_class"):
                self.status_label.remove_css_class("status-error")

            if error and hasattr(self.status_label, "add_css_class"):
                self.status_label.add_css_class("status-error")

            self.status_label.set_text(message)

    def handle_connection_lost(self) -> None:
        self.set_status(_("Connection to daemon lost."), error=True)
        self.on_quit()

    def save_last_session_state(self) -> None:
        """Persist the remembered greeter username and session selection.

        Args:
            None.

        Returns:
            Nothing. The helper writes the current `last-session` file when the
            greeter has a configured state file path.
        """
        state_file = getattr(self, "state_file", "")

        if not state_file:
            return

        try:
            wldm.state.save_last_session_file(state_file, self.last_username, self.last_session_command)
        except OSError as e:
            logger.warning("unable to save last-session state in %s: %s", state_file, e)

    def log_protocol_error(self, context: str, raw: bytes, error: Exception) -> None:
        logger.critical("%s: %s; raw=%r", context, error, raw)

    def set_auth_state(self, busy: bool) -> None:
        self.auth_in_progress = busy
        self.update_auth_widgets()

        if busy:
            self.set_status(_("Authenticating..."))

    def update_auth_widgets(self) -> None:
        """Apply the current auth/conversation sensitivity policy to widgets."""
        conversation_pending = getattr(self, "conversation_pending", False)
        session_ready = getattr(self, "session_ready", False)
        prompt_style = getattr(self, "conversation_prompt_style", "")
        prompt_text = getattr(self, "conversation_prompt_text", "")
        prompt_requires_input = conversation_pending and prompt_style in {"secret", "visible"}
        prompt_is_secret = prompt_style == "secret"
        username_locked = self.auth_in_progress or conversation_pending or session_ready

        username_entry = getattr(self, "username_entry", None)
        if username_entry is not None and hasattr(username_entry, "set_sensitive"):
            username_entry.set_sensitive(not username_locked)

        sessions_entry = getattr(self, "sessions_entry", None)
        if sessions_entry is not None and hasattr(sessions_entry, "set_sensitive"):
            sessions_entry.set_sensitive(session_ready and not self.auth_in_progress)
        if sessions_entry is not None and hasattr(sessions_entry, "set_visible"):
            sessions_entry.set_visible(session_ready)

        password_entry = getattr(self, "password_entry", None)
        if password_entry is not None and hasattr(password_entry, "set_sensitive"):
            password_entry.set_sensitive(prompt_requires_input and not self.auth_in_progress)
        if password_entry is not None and hasattr(password_entry, "set_visible"):
            password_entry.set_visible(prompt_requires_input)
        if password_entry is not None and hasattr(password_entry, "set_visibility"):
            password_entry.set_visibility(not prompt_is_secret)
        if password_entry is not None and hasattr(password_entry, "set_placeholder_text"):
            if prompt_style == "secret":
                password_entry.set_placeholder_text(prompt_text or _("Password"))
            elif prompt_style == "visible":
                password_entry.set_placeholder_text(prompt_text or _("Response"))
            else:
                password_entry.set_placeholder_text("")

        login_button = getattr(self, "login_button", None)
        if login_button is not None and hasattr(login_button, "set_sensitive"):
            login_button.set_sensitive(not self.auth_in_progress)
        if login_button is not None and hasattr(login_button, "set_label"):
            if session_ready:
                login_button.set_label(_("Start session"))
            elif conversation_pending:
                login_button.set_label(_("Continue"))
            else:
                login_button.set_label(_("Next"))

        session_label = getattr(self, "session_label", None)
        if session_label is not None and hasattr(session_label, "set_visible"):
            session_label.set_visible(session_ready)

    def clear_conversation_state(self) -> None:
        """Forget the current multi-step authentication state."""
        self.conversation_pending = False
        self.conversation_prompt_style = ""
        self.conversation_prompt_text = ""
        self.session_ready = False
        self.auth_username = ""
        self.update_auth_widgets()

    def set_conversation_prompt(self, style: str, text: str) -> None:
        """Remember one pending prompt and update the greeter status."""
        self.conversation_pending = True
        self.session_ready = False
        self.conversation_prompt_style = style
        self.conversation_prompt_text = text
        self.update_auth_widgets()

        if self.password_entry is not None:
            self.password_entry.set_text("")

        if text:
            self.set_status(text, error=style == "error")

        if self.password_entry is not None and style in {"secret", "visible"} and hasattr(self.password_entry, "grab_focus"):
            self.password_entry.grab_focus()

    def set_session_ready(self) -> None:
        """Move the greeter to the post-auth session selection stage."""
        self.conversation_pending = False
        self.conversation_prompt_style = ""
        self.conversation_prompt_text = ""
        self.session_ready = True
        self.update_auth_widgets()
        self.set_status(_("Authentication accepted. Select a session."))

    def update_session_summary(self) -> None:
        if self.session_label is None:
            return

        item = "Default shell"
        command = self.get_session_command()
        description = ""

        if command:
            item = command
            entry = self.get_selected_session()
            if entry:
                description = str(entry.get("comment", ""))

        if description:
            self.session_label.set_text(_("Session: %(description)s\nCommand: %(command)s")
                                        % {"description": description, "command": item})
        else:
            self.session_label.set_text(_("Session command: %(command)s") % {"command": item})

    def selected_session_data(self) -> tuple[str, list[str]]:
        """Return the current session command and desktop names.

        Args:
            None.

        Returns:
            Tuple of selected session command and desktop name list.
        """
        command = self.get_session_command()
        desktop_names: list[str] = []
        session_entry = self.get_selected_session()

        if session_entry is not None:
            desktop_names = list(session_entry.get("desktop_names", []))

        return command, desktop_names

    def refresh_sessions(self, username: str = "", preferred_command: str = "") -> None:
        current_name = ""
        current_command = ""
        entry = self.get_selected_session()

        if entry is not None:
            current_name = str(entry["name"])
            current_command = str(entry["command"])

        if not preferred_command:
            preferred_command = current_command or self.last_session_command
        else:
            current_name = ""

        self.sessions = wldm.sessions.desktop_sessions(username)

        if self.sessions_entry is not None and hasattr(self.sessions_entry, "set_model"):
            name_store = Gtk.StringList()

            for session in self.sessions:
                name_store.append(str(session["name"]))

            self.sessions_entry.set_model(name_store)

            if self.sessions:
                selected = 0

                for index, session in enumerate(self.sessions):
                    if session["name"] == current_name:
                        selected = index
                        break

                    if not current_name and preferred_command and session["command"] == preferred_command:
                        selected = index
                        break

                self.sessions_entry.set_selected(selected)

        self.update_session_summary()

    def update_identity_preview(self) -> None:
        username = ""

        if self.username_entry is not None:
            username = self.username_entry.get_text().strip()

        profile = account_service_profile(username)

        if self.identity_preview is not None:
            self.identity_preview.set_visible(profile is not None)

        if profile is None:
            return

        display_name = profile["display_name"]
        avatar_text = username[:1].upper()

        if self.identity_label is not None:
            self.identity_label.set_text(display_name)

        if self.avatar_label is not None:
            self.avatar_label.set_text(avatar_text)

    def update_action_buttons(self) -> None:
        button_actions = [
            (getattr(self, "quit_button", None), wldm.protocol.ACTION_POWEROFF),
            (getattr(self, "reboot_button", None), wldm.protocol.ACTION_REBOOT),
            (getattr(self, "suspend_button", None), wldm.protocol.ACTION_SUSPEND),
            (getattr(self, "hibernate_button", None), wldm.protocol.ACTION_HIBERNATE),
        ]

        for button, action in button_actions:
            if button is not None and hasattr(button, "set_visible"):
                button.set_visible(action in self.actions)

    def update_clock(self) -> None:
        if self.date_label is not None:
            self.date_label.set_text(time.strftime("%A, %d %B"))

        if self.time_label is not None:
            self.time_label.set_text(time.strftime("%H:%M"))

    def update_keyboard_indicator(self) -> None:
        keyboard_label = getattr(self, "keyboard_label", None)

        if keyboard_label is None:
            return

        layouts, active_index = keyboard_state()

        if not layouts or active_index < 0 or active_index >= len(layouts):
            keyboard_label.set_text("")
            keyboard_label.set_tooltip_text(None)
            keyboard_label.set_visible(False)
            return

        current = layouts[active_index]
        max_width = max(len(layout.short_name) for layout in layouts)

        keyboard_label.set_text(current.short_name.upper())
        keyboard_label.set_tooltip_text(current.long_name)
        keyboard_label.set_width_chars(max_width)
        keyboard_label.set_visible(True)

    def on_clock_tick(self) -> bool:
        self.poll_events()
        self.update_clock()
        self.update_keyboard_indicator()

        return not self.quit

    def poll_events(self) -> None:
        connection_lost = False
        acquired = False

        try:
            acquired = lock.acquire(blocking=False)
            if not acquired:
                return

            while hasattr(self.client, "can_read") and self.client.can_read():
                try:
                    message = self.client.read_message()

                except wldm.protocol.ProtocolError as e:
                    self.log_protocol_error("bad greeter event message", e.raw, e)
                    connection_lost = True
                    break

                if message is None:
                    connection_lost = True
                    break

                if wldm.protocol.is_event(message):
                    self.handle_event(message)
                    continue

                logger.debug("unexpected protocol message while idle: %s", message)

        except Exception as e:
            logger.critical("unexpected polling error: %r", e)
            connection_lost = True

        finally:
            if acquired:
                lock.release()

        if connection_lost:
            self.handle_connection_lost()

    def handle_event(self, event: Dict[str, Any]) -> None:
        if not wldm.protocol.is_event(event):
            return

        payload = event["payload"]
        event_name = event["event"]

        logger.debug("protocol event: %s", event)

        if event_name == wldm.protocol.EVENT_SESSION_STARTING:
            self.set_auth_state(True)
            self.set_status(_("Starting session..."))
            return

        if event_name == wldm.protocol.EVENT_SESSION_FINISHED:
            self.set_auth_state(False)
            self.clear_conversation_state()

            if not bool(payload.get("failed", False)):
                if self.username_entry is not None:
                    current_username = self.username_entry.get_text().strip()
                    if current_username:
                        self.last_username = current_username

                self.save_last_session_state()

            if self.username_entry is not None:
                self.username_entry.set_text(self.last_username)

                if hasattr(self.username_entry, "grab_focus"):
                    self.username_entry.grab_focus()

                clear_entry_selection(self.username_entry)

            if self.password_entry is not None:
                self.password_entry.set_text("")

            self.refresh_sessions(self.last_username, preferred_command=self.last_session_command)

            status_message = str(payload.get("message", _("Session finished.")))

            self.set_status(status_message, error=bool(payload.get("failed", False)))
            return

    def run(self) -> None:
        self.app.run()

    def on_activate(self, app: Gtk.Application) -> None:
        global resource_path

        try:
            builder = load_builder_from_resource_path()
            bindings = self.collect_theme_widgets(builder)

        except Exception as exc:
            fallback_path = default_resource_path()
            theme = greeter_theme()

            if theme == "default" or not fallback_path or fallback_path == resource_path:
                raise

            logger.warning("theme '%s' is invalid, falling back to default: %s", theme, exc)
            resource_path = fallback_path
            setup_greeter_i18n()

            builder = load_builder_from_resource_path()
            bindings = self.collect_theme_widgets(builder)

        window: Any = None

        for spec, widget in bindings:
            name = str(spec["name"])

            if name == "main_window":
                window = widget
                continue

            for signal, handler in spec.get("signals", ()):
                widget.connect(signal, handler)

            setattr(self, name, widget)

        window.set_application(app)

        if self.login_button is not None and hasattr(window, "set_default_widget"):
            window.set_default_widget(self.login_button)

        if self.hostname_label is not None:
            self.hostname_label.set_text(socket.gethostname())

        self.update_clock()
        self.update_keyboard_indicator()

        GLib.timeout_add_seconds(1, self.on_clock_tick)

        if self.username_entry is not None and self.last_username:
            self.username_entry.set_text(self.last_username)

        self.refresh_sessions(self.last_username, preferred_command=self.last_session_command)
        self.update_identity_preview()
        self.update_action_buttons()
        self.update_auth_widgets()
        self.set_status("")

        if self.username_entry is not None and hasattr(self.username_entry, "grab_focus"):
            self.username_entry.grab_focus()

            if self.last_username:
                clear_entry_selection(self.username_entry)

        window.present()

    def get_selected_session_name(self) -> str:
        if self.sessions_entry is None:
            return ""

        item = self.sessions_entry.get_selected_item()

        if item is None:
            return ""

        return str(item.get_string())

    def get_selected_session(self) -> Optional[Dict[str, Any]]:
        name = self.get_selected_session_name()

        if not name:
            return None

        for entry in self.sessions:
            if entry["name"] == name:
                return entry

        return None

    def get_session_command(self) -> str:
        entry = self.get_selected_session()
        if entry is None:
            return ""
        return str(entry["command"])

    # pylint: disable-next=unused-argument
    def on_session_changed(self, *args: Any) -> None:
        self.update_session_summary()

    # pylint: disable-next=unused-argument
    def on_username_activate(self, *args: Any) -> None:
        if hasattr(self, "username_entry"):
            self.on_login_clicked()
            return

        if self.password_entry is not None and hasattr(self.password_entry, "grab_focus"):
            self.password_entry.grab_focus()

    # pylint: disable-next=unused-argument
    def on_username_changed(self, *args: Any) -> None:
        username = ""

        if self.username_entry is not None:
            username = self.username_entry.get_text().strip()

        self.refresh_sessions(username)
        self.update_identity_preview()

    def send_recv_answer(self, data: Dict[str, Any]) -> Dict[str, Any]:
        answer = {}
        connection_lost = False

        try:
            lock.acquire()
            self.client.write_message(data)

            while True:
                try:
                    message = self.client.read_message()

                except wldm.protocol.ProtocolError as e:
                    self.log_protocol_error("bad greeter response message", e.raw, e)
                    connection_lost = True
                    break

                if message is None:
                    connection_lost = True
                    break

                if wldm.protocol.is_event(message):
                    self.handle_event(message)
                    continue

                if wldm.protocol.is_response(message, data):
                    answer = message
                    break

        except Exception as e:
            logger.critical("unexpected error: %r", e)
            connection_lost = True

        finally:
            lock.release()

        if connection_lost:
            self.handle_connection_lost()

        return answer

    def read_prompt_response(self) -> wldm.secret.SecretBytes | None:
        """Read one reply for the current pending auth prompt."""
        if self.password_entry is None:
            return None

        style = self.conversation_prompt_style

        if style in {"info", "error"}:
            self.password_entry.set_text("")
            return wldm.secret.SecretBytes()

        response = gtk_ffi.read_password_secret(self.password_entry)

        if len(response) == 0:
            self.set_status(self.conversation_prompt_text or _("Enter a response."), error=True)

            if hasattr(self.password_entry, "grab_focus"):
                self.password_entry.grab_focus()

            response.clear()
            return None

        if wldm.protocol.auth_field_is_too_long(response):
            self.set_status(
                _("Response must be %(limit)d bytes or less.")
                % {"limit": wldm.protocol.AUTH_FIELD_MAX_LENGTH},
                error=True,
            )

            if hasattr(self.password_entry, "grab_focus"):
                self.password_entry.grab_focus()

            response.clear()
            return None

        return response

    def start_selected_session(self, command: str, desktop_names: list[str]) -> bool:
        """Ask the daemon to start one already-authenticated session."""
        start_request = wldm.protocol.new_request(
            wldm.protocol.ACTION_START_SESSION,
            {
                "command": command,
                "desktop_names": desktop_names,
            },
        )
        start_answer = self.send_recv_answer(start_request)

        return bool(start_answer.get("ok"))

    def handle_conversation_answer(self, answer: Dict[str, Any]) -> str:
        """Advance the current greeter-side conversation state from one reply."""
        if not answer.get("ok"):
            self.clear_conversation_state()
            return "failed"

        payload = answer.get("payload", {})
        state = str(payload.get("state", ""))

        if state == "pending":
            message = payload.get("message", {})
            style = str(message.get("style", ""))
            text = str(message.get("text", ""))

            if style not in {"secret", "visible", "info", "error"}:
                logger.warning("unsupported auth conversation step: %s", answer)
                self.clear_conversation_state()
                return "failed"

            self.set_conversation_prompt(style, text)
            return "pending"

        if state == "ready":
            self.set_session_ready()
            return "ready"

        logger.warning("unexpected auth conversation state: %s", answer)
        self.clear_conversation_state()
        return "failed"

    # pylint: disable-next=unused-argument
    def on_login_clicked(self, *args: Any) -> None:
        if self.username_entry is None or self.password_entry is None:
            return

        if self.auth_in_progress:
            return

        if getattr(self, "session_ready", False):
            command, desktop_names = self.selected_session_data()
            self.set_auth_state(True)

            if self.start_selected_session(command, desktop_names):
                self.last_username = self.auth_username.strip()
                self.last_session_command = command
                self.username_entry.set_text("")
                self.set_status(_("Authentication accepted. Waiting for session..."))
                return

            self.set_auth_state(False)
            self.set_status(_("Unable to start session."), error=True)
            return

        if getattr(self, "conversation_pending", False):
            response = self.read_prompt_response()
            if response is None:
                return

            self.password_entry.set_text("")
            self.set_auth_state(True)

            try:
                answer = self.send_recv_answer(
                    wldm.protocol.new_request(
                        wldm.protocol.ACTION_CONTINUE_SESSION,
                        {"response": response},
                    )
                )
            finally:
                response.clear()

            self.set_auth_state(False)
            result = self.handle_conversation_answer(answer)

            if result == "pending":
                return

            if result == "ready":
                return

            if hasattr(self.password_entry, "grab_focus"):
                self.password_entry.grab_focus()
            self.set_status(_("Authentication failed."), error=True)
            return

        username = self.username_entry.get_text()

        if len(username) == 0:
            self.set_status(_("Enter a username."), error=True)
            return

        if wldm.protocol.auth_field_is_too_long(username):
            self.set_status(
                _("Username must be %(limit)d bytes or less.")
                % {"limit": wldm.protocol.AUTH_FIELD_MAX_LENGTH},
                error=True,
            )
            return
        self.set_auth_state(True)
        self.auth_username = username

        create_request = wldm.protocol.new_request(
            wldm.protocol.ACTION_CREATE_SESSION,
            {"username": username},
        )
        create_answer = self.send_recv_answer(create_request)

        self.set_auth_state(False)
        result = self.handle_conversation_answer(create_answer)
        if result == "pending":
            return
        if result == "ready":
            return
        self.set_status(_("Authentication failed."), error=True)
        if hasattr(self.password_entry, "grab_focus"):
            self.password_entry.grab_focus()

    # pylint: disable-next=unused-argument
    def on_quit(self, *args: Any) -> None:
        self.quit = True
        self.client.close()
        if hasattr(self, "app"):
            self.app.quit()

    def request_system_action(self, action: str, status_message: str) -> None:
        self.set_status(status_message)

        answer = self.send_recv_answer(wldm.protocol.new_request(action, {}))
        logger.debug("client %s answer: %s", action, answer)

        if not answer.get("ok") or not answer.get("payload", {}).get("accepted"):
            self.set_status(_("Unable to %(action)s.") % {"action": action}, error=True)

    # pylint: disable-next=unused-argument
    def on_poweroff_clicked(self, *args: Any) -> None:
        self.request_system_action(wldm.protocol.ACTION_POWEROFF, _("Powering off..."))

    # pylint: disable-next=unused-argument
    def on_reboot_clicked(self, *args: Any) -> None:
        self.request_system_action(wldm.protocol.ACTION_REBOOT, _("Rebooting..."))

    # pylint: disable-next=unused-argument
    def on_suspend_clicked(self, *args: Any) -> None:
        self.request_system_action(wldm.protocol.ACTION_SUSPEND, _("Suspending..."))

    # pylint: disable-next=unused-argument
    def on_hibernate_clicked(self, *args: Any) -> None:
        self.request_system_action(wldm.protocol.ACTION_HIBERNATE, _("Hibernating..."))


def cmd_main(_parser: argparse.Namespace) -> int:
    global resource_path

    setup_greeter_logging()
    resource_path = themed_resource_path()

    if not os.path.isdir(resource_path):
        logger.critical("resource directory does not exist: %s", resource_path)
        return wldm.EX_FAILURE

    setup_greeter_i18n()

    if "WLDM_SOCKET_FD" not in os.environ:
        logger.critical("environ variable `WLDM_SOCKET_FD' not specified")
        return wldm.EX_FAILURE

    logger.debug("Resource path: %s", resource_path)

    css_file = os.path.join(resource_path, "style.css")

    if os.path.isfile(css_file):
        css_provider = Gtk.CssProvider()
        css_provider.load_from_path(css_file)
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(),
                                                  css_provider,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    app = LoginApp()
    app.run()

    return wldm.EX_SUCCESS
