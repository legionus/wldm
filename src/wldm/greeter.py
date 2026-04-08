#!/usr/bin/env python
# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026  Alexey Gladkov <legion@kernel.org>

import argparse
import gettext
import locale
import os
import os.path
import select
import socket
import sys
import threading
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
import wldm.greeter_auth as greeter_auth
# pylint: disable-next=wrong-import-position
import wldm.greeter_client as greeter_client
# pylint: disable-next=wrong-import-position
import wldm.greeter_ui as greeter_ui
# pylint: disable-next=wrong-import-position
import wldm.inifile
# pylint: disable-next=wrong-import-position
import wldm.greeter_protocol as greeter_protocol
# pylint: disable-next=wrong-import-position
import wldm.policy
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
        self.sock.sendall(greeter_protocol.encode_message(message))

    def read_message(self) -> Dict[str, Any] | None:
        return greeter_protocol.read_message_socket(self.sock)

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


class GreeterApp(greeter_ui.GreeterUI):
    def __init__(self, client: Optional[Any]=None) -> None:
        super().__init__()
        self.app = Gtk.Application(application_id=wldm.policy.GREETER_APP_ID,
                                   flags=Gio.ApplicationFlags.FLAGS_NONE)

        self.app.connect('activate', self.on_activate)

        self.sessions = wldm.sessions.desktop_sessions()
        self.client = client if client is not None else new_ipc_client()

        self.quit = False
        self.actions = available_actions()
        self.state_file = configured_state_file()

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
                "name": "cancel_button",
                "methods": ("connect", "set_sensitive", "set_visible"),
                "signals": (("clicked", self.on_cancel_clicked),),
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

    def handle_connection_lost(self) -> None:
        greeter_client.handle_connection_lost(self)

    def log_protocol_error(self, context: str, raw: bytes, error: Exception) -> None:
        greeter_client.log_protocol_error(self, context, raw, error)

    def on_clock_tick(self) -> bool:
        self.poll_events()
        self.update_clock()
        self.update_keyboard_indicator()

        return not self.quit

    def poll_events(self) -> None:
        greeter_client.poll_events(self, lock)

    def handle_event(self, event: Dict[str, Any]) -> None:
        greeter_client.handle_event(self, event, clear_entry_selection)

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

    @staticmethod
    def clear_entry_selection(entry: Any) -> None:
        clear_entry_selection(entry)

    @staticmethod
    def account_service_profile(username: str) -> Dict[str, str] | None:
        return account_service_profile(username)

    gtk = Gtk
    greeter_protocol = greeter_protocol

    # pylint: disable-next=unused-argument
    def on_cancel_clicked(self, *args: Any) -> None:
        greeter_auth.on_cancel_clicked(self)

    def send_recv_answer(self, data: Dict[str, Any]) -> Dict[str, Any]:
        return greeter_client.send_recv_answer(self, data, lock)

    def read_prompt_response(self) -> wldm.secret.SecretBytes | None:
        """Read one reply for the current pending auth prompt."""
        return greeter_auth.read_prompt_response(self)

    def start_selected_session(self, command: str, desktop_names: list[str]) -> bool:
        """Ask the daemon to start one already-authenticated session."""
        return greeter_auth.start_selected_session(self, command, desktop_names)

    def handle_conversation_answer(self, answer: Dict[str, Any]) -> str:
        """Advance the current greeter-side conversation state from one reply."""
        return greeter_auth.handle_conversation_answer(self, answer)

    # pylint: disable-next=unused-argument
    def on_login_clicked(self, *args: Any) -> None:
        greeter_auth.on_login_clicked(self)

    # pylint: disable-next=unused-argument
    def on_quit(self, *args: Any) -> None:
        self.quit = True
        self.client.close()
        if hasattr(self, "app"):
            self.app.quit()

    def request_system_action(self, action: str, status_message: str) -> None:
        self.set_status(status_message)

        answer = self.send_recv_answer(greeter_protocol.new_request(action, {}))
        logger.debug("client %s answer: %s", action, answer)

        if not answer.get("ok") or not answer.get("payload", {}).get("accepted"):
            self.set_status(_("Unable to %(action)s.") % {"action": action}, error=True)

    # pylint: disable-next=unused-argument
    def on_poweroff_clicked(self, *args: Any) -> None:
        self.request_system_action(greeter_protocol.ACTION_POWEROFF, _("Powering off..."))

    # pylint: disable-next=unused-argument
    def on_reboot_clicked(self, *args: Any) -> None:
        self.request_system_action(greeter_protocol.ACTION_REBOOT, _("Rebooting..."))

    # pylint: disable-next=unused-argument
    def on_suspend_clicked(self, *args: Any) -> None:
        self.request_system_action(greeter_protocol.ACTION_SUSPEND, _("Suspending..."))

    # pylint: disable-next=unused-argument
    def on_hibernate_clicked(self, *args: Any) -> None:
        self.request_system_action(greeter_protocol.ACTION_HIBERNATE, _("Hibernating..."))


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

    app = GreeterApp()
    app.run()

    return wldm.EX_SUCCESS
