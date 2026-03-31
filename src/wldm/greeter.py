#!/usr/bin/env python
# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026  Alexey Gladkov <legion@kernel.org>

import argparse
import configparser
import gettext
import json
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

# pylint: disable-next=wrong-import-position
from gi.repository import Gtk, Gdk, Gio, GLib  # type: ignore[import-untyped]

# pylint: disable-next=wrong-import-position
import wldm
# pylint: disable-next=wrong-import-position
import wldm.policy
# pylint: disable-next=wrong-import-position
import wldm.protocol
# pylint: disable-next=wrong-import-position
import wldm.sessions

logger = wldm.logger
resource_path: str
lock = threading.Lock()
GETTEXT_DOMAIN = "wldm"
_ = gettext.gettext

REQUIRED_THEME_WIDGETS = [
    "main_window",
    "username_entry",
    "password_entry",
    "login_button",
]

WIDGET_BINDINGS = [
    "username_entry",
    "password_entry",
    "sessions_entry",
    "status_label",
    "login_button",
    "quit_button",
    "reboot_button",
    "suspend_button",
    "hibernate_button",
    "hostname_label",
    "date_label",
    "time_label",
    "session_label",
    "identity_label",
    "avatar_label",
]


def account_service_profile(username: str) -> Dict[str, str]:
    profile = {
        "display_name": username,
        "avatar_path": "",
    }

    if not username:
        return profile

    path = os.path.join(wldm.policy.ACCOUNTS_SERVICE_USERS_DIR, username)
    if not os.path.isfile(path):
        return profile

    data = configparser.ConfigParser()
    try:
        data.read(path)
    except OSError:
        return profile

    profile["display_name"] = data.get("User", "RealName", fallback=username) or username
    avatar_path = data.get("User", "Icon", fallback="")
    if avatar_path and os.path.isfile(avatar_path):
        profile["avatar_path"] = avatar_path

    return profile

class SocketClient:
    def __init__(self, path: str) -> None:
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.connect(path)
        self.reader = self.sock.makefile("r", encoding="utf-8")

    def writeline(self, data: str) -> None:
        self.sock.sendall((data + "\n").encode())

    def readline(self) -> str:
        return self.reader.readline()

    def can_read(self) -> bool:
        readable, _, _ = select.select([self.sock], [], [], 0.0)
        return self.sock in readable

    def close(self) -> None:
        try:
            self.reader.close()
        except Exception:
            pass
        self.sock.close()


def new_ipc_client() -> Any:
    socket_path = os.environ.get("WLDM_SOCKET", "")
    if not socket_path:
        raise RuntimeError("environ variable `WLDM_SOCKET' not specified")
    return SocketClient(socket_path)


def available_actions() -> set[str]:
    value = os.environ.get("WLDM_ACTIONS", "")
    return {item for item in value.split(":") if item}


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
    if "WLDM_LOCALE_PATH" in os.environ:
        return os.path.abspath(os.environ["WLDM_LOCALE_PATH"])

    theme_locale = os.path.join(resource_path, "locale")
    if os.path.isdir(theme_locale):
        return theme_locale

    return os.path.join(sys.prefix, "share", "locale")


def setup_greeter_i18n() -> None:
    try:
        locale.setlocale(locale.LC_ALL, "")
    except locale.Error:
        pass

    gettext.bindtextdomain(GETTEXT_DOMAIN, greeter_locale_path())
    gettext.textdomain(GETTEXT_DOMAIN)


def default_resource_path() -> str:
    if "WLDM_RESOURCES_PATH" in os.environ:
        return os.path.abspath(os.environ["WLDM_RESOURCES_PATH"])
    return os.path.join(sys.prefix, "share", "wldm", "resources")


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


def validate_theme_widgets(builder: Any) -> None:
    missing = []

    for name in REQUIRED_THEME_WIDGETS:
        try:
            widget = builder.get_object(name)
        except Exception:
            widget = None
        if widget is None:
            missing.append(name)

    if missing:
        raise RuntimeError(
            f"theme '{greeter_theme()}' is missing required widget(s): {', '.join(missing)}"
        )


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
        self.session_label:  Optional[Any] = None
        self.identity_label: Optional[Any] = None
        self.avatar_label:   Optional[Any] = None

        self.sessions = wldm.sessions.desktop_sessions()
        self.client = client if client is not None else new_ipc_client()

        self.quit = False
        self.auth_in_progress = False
        self.actions = available_actions()

    def set_status(self, message: str) -> None:
        if self.status_label is not None:
            self.status_label.set_text(message)

    def handle_connection_lost(self) -> None:
        self.set_status(_("Connection to daemon lost."))
        self.on_quit()

    def log_protocol_error(self, context: str, raw: str, error: Exception) -> None:
        logger.critical("%s: %s; raw=%r", context, error, raw.rstrip("\n"))

    def set_auth_state(self, busy: bool) -> None:
        self.auth_in_progress = busy

        widgets = [
            getattr(self, "username_entry", None),
            getattr(self, "password_entry", None),
            getattr(self, "sessions_entry", None),
            getattr(self, "login_button", None),
        ]
        for widget in widgets:
            if widget is not None and hasattr(widget, "set_sensitive"):
                widget.set_sensitive(not busy)

        if busy:
            self.set_status(_("Authenticating..."))

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

    def refresh_sessions(self, username: str = "") -> None:
        current_name = ""
        entry = self.get_selected_session()
        if entry is not None:
            current_name = str(entry["name"])

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
                self.sessions_entry.set_selected(selected)

        self.update_session_summary()

    def update_identity_preview(self) -> None:
        username = ""
        if self.username_entry is not None:
            username = self.username_entry.get_text().strip()

        profile = account_service_profile(username)
        display_name = profile["display_name"] if username else _("Type a username to preview the account")
        avatar_text = username[:1].upper() if username else "?"

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

    def on_clock_tick(self) -> bool:
        self.poll_events()
        self.update_clock()
        return not self.quit

    def poll_events(self) -> None:
        connection_lost = False
        acquired = False

        try:
            acquired = lock.acquire(blocking=False)
            if not acquired:
                return

            while hasattr(self.client, "can_read") and self.client.can_read():
                line = self.client.readline()
                if len(line) == 0:
                    connection_lost = True
                    break

                try:
                    message = wldm.protocol.decode_message(line)
                except (json.decoder.JSONDecodeError, ValueError) as e:
                    self.log_protocol_error("bad greeter event message", line, e)
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
            self.set_status(_("Starting session for %(username)s...") %
                            {"username": payload.get("username", "user")})
            return

        if event_name == wldm.protocol.EVENT_SESSION_FINISHED:
            self.set_auth_state(False)
            if self.username_entry is not None:
                self.username_entry.set_text("")
            if self.password_entry is not None:
                self.password_entry.set_text("")
                if hasattr(self.password_entry, "grab_focus"):
                    self.password_entry.grab_focus()
            status_message = str(payload.get("message", _("Session finished.")))
            self.set_status(status_message)
            return

    def run(self) -> None:
        self.app.run()

    def on_activate(self, app: Gtk.Application) -> None:
        builder = Gtk.Builder.new()
        builder.set_translation_domain(GETTEXT_DOMAIN)
        builder.add_from_file(os.path.join(resource_path, "greeter.ui"))
        validate_theme_widgets(builder)

        def get_object(name: str) -> Optional[Any]:
            try:
                return builder.get_object(name)
            except Exception:
                return None

        window = get_object("main_window")
        if window is None:
            raise RuntimeError("greeter.ui is missing main_window")
        window.set_application(app)

        for name in WIDGET_BINDINGS:
            setattr(self, name, get_object(name))

        if self.hostname_label is not None:
            self.hostname_label.set_text(socket.gethostname())
        self.update_clock()
        GLib.timeout_add_seconds(1, self.on_clock_tick)

        if self.sessions_entry is not None:
            self.sessions_entry.connect("notify::selected-item", self.on_session_changed)

        for widget, callback in [
            (self.login_button, self.on_login_clicked),
            (self.quit_button, self.on_poweroff_clicked),
            (self.reboot_button, self.on_reboot_clicked),
            (self.suspend_button, self.on_suspend_clicked),
            (self.hibernate_button, self.on_hibernate_clicked),
        ]:
            if widget is not None:
                widget.connect("clicked", callback)

        if self.password_entry is not None:
            self.password_entry.connect("activate", self.on_login_clicked)
        if self.sessions_entry is not None:
            self.sessions_entry.connect("activate", self.on_login_clicked)
        if self.username_entry is not None:
            self.username_entry.connect("changed", self.on_username_changed)

        self.refresh_sessions()
        self.update_identity_preview()
        self.update_action_buttons()
        self.set_status("")

        if self.username_entry is not None and hasattr(self.username_entry, "grab_focus"):
            self.username_entry.grab_focus()

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
            self.client.writeline(wldm.protocol.encode_message(data))

            while True:
                line = self.client.readline()
                if len(line) == 0:
                    connection_lost = True
                    break

                try:
                    message = wldm.protocol.decode_message(line)
                except (json.decoder.JSONDecodeError, ValueError) as e:
                    self.log_protocol_error("bad greeter response message", line, e)
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

    # pylint: disable-next=unused-argument
    def on_login_clicked(self, *args: Any) -> None:
        if self.username_entry is None or self.password_entry is None:
            return
        if self.auth_in_progress:
            return

        username = self.username_entry.get_text()
        password = self.password_entry.get_text()

        data = {
                "username": username,
                "password": password,
                "command":  self.get_session_command(),
                "desktop_names": [],
                }
        session_entry = self.get_selected_session()
        if session_entry is not None:
            data["desktop_names"] = list(session_entry.get("desktop_names", []))

        logger.debug("client request: username=[%s] password=[%s] command=[%s]",
                     data["username"], '*' * len(data["password"]),
                     data["command"])

        if len(data["username"]) == 0:
            self.set_status(_("Enter a username."))
            return

        if len(data["password"]) == 0:
            self.set_status(_("Enter a password."))
            if hasattr(self.password_entry, "grab_focus"):
                self.password_entry.grab_focus()
            return

        self.password_entry.set_text("")
        self.set_auth_state(True)
        answer = self.send_recv_answer(wldm.protocol.new_request(wldm.protocol.ACTION_AUTH, data))
        logger.debug("client answer: %s", answer)

        if answer.get("ok") and answer.get("payload", {}).get("verified"):
            self.username_entry.set_text("")
            status_message = _("Authentication accepted. Waiting for session...")
        else:
            self.set_auth_state(False)
            status_message = _("Authentication failed.")
            self.password_entry.grab_focus()

        self.set_status(status_message)

    # pylint: disable-next=unused-argument
    def on_quit(self, *args: Any) -> None:
        self.quit = True
        self.client.close()
        self.app.quit()

    def request_system_action(self, action: str, status_message: str) -> None:
        self.set_status(status_message)
        answer = self.send_recv_answer(wldm.protocol.new_request(action, {}))
        logger.debug("client %s answer: %s", action, answer)

        if not answer.get("ok") or not answer.get("payload", {}).get("accepted"):
            self.set_status(_("Unable to %(action)s.") % {"action": action})

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

    if "WLDM_SOCKET" not in os.environ:
        logger.critical("environ variable `WLDM_SOCKET' not specified")
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
