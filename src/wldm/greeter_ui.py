#!/usr/bin/env python
# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026  Alexey Gladkov <legion@kernel.org>

import gettext
import time
from typing import Any, Optional

import wldm.greeter_account as greeter_account
import wldm
import wldm.greeter_keyboard as greeter_keyboard
import wldm.sessions
import wldm.state

_ = gettext.gettext
logger = wldm.logger


class GreeterUI:
    """Widget and state helpers shared by the greeter application."""
    gtk: Any
    greeter_protocol: Any

    def __init__(self) -> None:
        """Initialize greeter widget references and UI-facing state."""
        self.username_entry: Optional[Any] = None
        self.password_entry: Optional[Any] = None
        self.status_label: Optional[Any] = None
        self.sessions_entry: Optional[Any] = None
        self.login_button: Optional[Any] = None
        self.cancel_button: Optional[Any] = None
        self.quit_button: Optional[Any] = None
        self.reboot_button: Optional[Any] = None
        self.suspend_button: Optional[Any] = None
        self.hibernate_button: Optional[Any] = None
        self.hostname_label: Optional[Any] = None
        self.date_label: Optional[Any] = None
        self.time_label: Optional[Any] = None
        self.keyboard_label: Optional[Any] = None
        self.identity_preview: Optional[Any] = None
        self.identity_label: Optional[Any] = None
        self.avatar_label: Optional[Any] = None
        self.auth_in_progress: bool = False
        self.conversation_pending: bool = False
        self.conversation_prompt_style: str = ""
        self.conversation_prompt_text: str = ""
        self.session_ready: bool = False
        self.auth_username: str = ""
        self.last_username: str = ""
        self.last_session_command: str = ""
        self.state_file: str = ""
        self.actions: set[str] = set()
        self.sessions: list[dict[str, Any]] = []

    def clear_username_selection(self) -> None:
        """Move the username cursor to the end of the current entry text."""
        if self.username_entry is None:
            return

        if hasattr(self.username_entry, "select_region"):
            text = ""

            if hasattr(self.username_entry, "get_text"):
                text = str(self.username_entry.get_text())

            self.username_entry.select_region(len(text), len(text))
            return

        if hasattr(self.username_entry, "set_position"):
            self.username_entry.set_position(-1)

    def get_selected_session_name(self) -> str:
        """Return the selected session name from the current UI model."""
        if self.sessions_entry is None:
            return ""

        item = self.sessions_entry.get_selected_item()

        if item is None:
            return ""

        return str(item.get_string())

    def get_selected_session(self) -> dict[str, Any] | None:
        """Return the current selected desktop session entry."""
        name = self.get_selected_session_name()

        if not name:
            return None

        for entry in self.sessions:
            if entry["name"] == name:
                return entry

        return None

    def get_session_command(self) -> str:
        """Return the command for the selected desktop session."""
        entry = self.get_selected_session()

        if entry is None:
            return ""

        return str(entry["command"])

    def set_status(self, message: str, error: bool = False) -> None:
        """Update the greeter status line and its error styling."""
        if self.status_label is not None:
            if hasattr(self.status_label, "remove_css_class"):
                self.status_label.remove_css_class("status-error")

            if error and hasattr(self.status_label, "add_css_class"):
                self.status_label.add_css_class("status-error")

            self.status_label.set_text(message)

    def save_last_session_state(self) -> None:
        """Persist the remembered greeter username and session selection."""
        state_file = getattr(self, "state_file", "")

        if not state_file:
            return

        try:
            wldm.state.save_last_session_file(state_file, self.last_username, self.last_session_command)
        except OSError as e:
            logger.warning("unable to save last-session state in %s: %s", state_file, e)

    def set_auth_state(self, busy: bool) -> None:
        """Switch the greeter into or out of the busy authentication state."""
        self.auth_in_progress = busy
        self.update_auth_widgets()

        if busy:
            self.set_status(_("Authenticating..."))

    def call_widget_method(self, widget_name: str, method_name: str, *args: Any) -> None:
        """Call one widget method when the named widget exists and supports it."""
        widget = getattr(self, widget_name, None)

        if widget is None or not hasattr(widget, method_name):
            return

        getattr(widget, method_name)(*args)

    def update_auth_widgets(self) -> None:
        """Apply the current auth and conversation sensitivity policy to widgets."""
        conversation_pending = getattr(self, "conversation_pending", False)
        prompt_style = getattr(self, "conversation_prompt_style", "")
        prompt_text = getattr(self, "conversation_prompt_text", "")
        session_ready = getattr(self, "session_ready", False)
        auth_in_progress = getattr(self, "auth_in_progress", False)
        prompt_requires_input = conversation_pending and prompt_style in {"secret", "visible"}
        prompt_is_secret = prompt_style == "secret"
        username_locked = auth_in_progress or conversation_pending or session_ready

        self.call_widget_method("username_entry", "set_sensitive", not username_locked)
        self.call_widget_method("sessions_entry", "set_sensitive", session_ready and not auth_in_progress)
        self.call_widget_method("sessions_entry", "set_visible", session_ready)
        self.call_widget_method("password_entry", "set_sensitive", prompt_requires_input and not auth_in_progress)
        self.call_widget_method("password_entry", "set_visible", prompt_requires_input)
        self.call_widget_method("password_entry", "set_visibility", not prompt_is_secret)
        self.call_widget_method("password_entry", "set_show_peek_icon", prompt_requires_input)

        if prompt_style == "secret":
            self.call_widget_method("password_entry", "set_placeholder_text", prompt_text or _("Password"))
        elif prompt_style == "visible":
            self.call_widget_method("password_entry", "set_placeholder_text", prompt_text or _("Response"))
        else:
            self.call_widget_method("password_entry", "set_placeholder_text", "")

        self.call_widget_method("login_button", "set_sensitive", not auth_in_progress)
        if session_ready:
            self.call_widget_method("login_button", "set_label", _("Start session"))
        elif conversation_pending:
            self.call_widget_method("login_button", "set_label", _("Continue"))
        else:
            self.call_widget_method("login_button", "set_label", _("Next"))

        self.call_widget_method("cancel_button", "set_visible", conversation_pending or session_ready)
        self.call_widget_method("cancel_button", "set_sensitive", not auth_in_progress)

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

        if style in {"info", "error"} and text:
            self.set_status(text, error=style == "error")
        elif style in {"secret", "visible"}:
            self.set_status("")

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

    def reset_auth_flow(self) -> None:
        """Return the greeter to the initial username entry stage."""
        username = self.auth_username.strip()
        self.set_auth_state(False)
        self.clear_conversation_state()
        self.set_status("")

        if self.password_entry is not None:
            self.password_entry.set_text("")

        if self.username_entry is not None:
            self.username_entry.set_text(username)

            if hasattr(self.username_entry, "grab_focus"):
                self.username_entry.grab_focus()

            if username:
                self.clear_username_selection()

        self.refresh_sessions(username, preferred_command=self.last_session_command)
        self.update_identity_preview()

    def selected_session_data(self) -> tuple[str, list[str]]:
        """Return the current session command and desktop names."""
        command = self.get_session_command()
        desktop_names: list[str] = []
        session_entry = self.get_selected_session()

        if session_entry is not None:
            desktop_names = list(session_entry.get("desktop_names", []))

        return command, desktop_names

    def refresh_sessions(self, username: str = "", preferred_command: str = "") -> None:
        """Refresh the greeter session list for one username."""
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
            name_store = self.gtk.StringList()

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

    def update_identity_preview(self) -> None:
        """Refresh the identity preview for the currently typed username."""
        username = ""

        if self.username_entry is not None:
            username = self.username_entry.get_text().strip()

        profile = greeter_account.account_service_profile(username)

        if self.identity_preview is not None:
            self.identity_preview.set_visible(profile is not None)

        if profile is None:
            return

        if self.identity_label is not None:
            self.identity_label.set_text(profile["display_name"])

        if self.avatar_label is not None:
            self.avatar_label.set_text(username[:1].upper())

    def update_action_buttons(self) -> None:
        """Update power-action button visibility from the current action set."""
        button_actions = [
            (self.quit_button, self.greeter_protocol.ACTION_POWEROFF),
            (self.reboot_button, self.greeter_protocol.ACTION_REBOOT),
            (self.suspend_button, self.greeter_protocol.ACTION_SUSPEND),
            (self.hibernate_button, self.greeter_protocol.ACTION_HIBERNATE),
        ]

        for button, action in button_actions:
            if button is not None and hasattr(button, "set_visible"):
                button.set_visible(action in self.actions)

    def update_clock(self) -> None:
        """Refresh the greeter clock widgets."""
        if self.date_label is not None:
            self.date_label.set_text(time.strftime("%A, %d %B"))

        if self.time_label is not None:
            self.time_label.set_text(time.strftime("%H:%M"))

    def update_keyboard_indicator(self) -> None:
        """Refresh the greeter keyboard layout indicator."""
        keyboard_label = getattr(self, "keyboard_label", None)

        if keyboard_label is None:
            return

        layouts, active_index = greeter_keyboard.keyboard_state()

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
