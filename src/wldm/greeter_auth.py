#!/usr/bin/env python
# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026  Alexey Gladkov <legion@kernel.org>

import gettext
from typing import Any

import wldm
from wldm import _gtk_ffi as gtk_ffi
import wldm.greeter_protocol as greeter_protocol

_ = gettext.gettext
logger = wldm.logger


def read_prompt_response(app: Any) -> wldm.secret.SecretBytes | None:
    """Read one reply for the current pending auth prompt."""
    if app.password_entry is None:
        return None

    style = app.conversation_prompt_style

    if style in {"info", "error"}:
        app.password_entry.set_text("")
        return wldm.secret.SecretBytes()

    response = gtk_ffi.read_password_secret(app.password_entry)

    if len(response) == 0:
        app.set_status(app.conversation_prompt_text or _("Enter a response."), error=True)

        if hasattr(app.password_entry, "grab_focus"):
            app.password_entry.grab_focus()

        response.clear()
        return None

    if greeter_protocol.auth_field_is_too_long(response):
        app.set_status(
            _("Response must be %(limit)d bytes or less.")
            % {"limit": greeter_protocol.AUTH_FIELD_MAX_LENGTH},
            error=True,
        )

        if hasattr(app.password_entry, "grab_focus"):
            app.password_entry.grab_focus()

        response.clear()
        return None

    return response


def start_selected_session(app: Any, command: str, desktop_names: list[str]) -> bool:
    """Ask the daemon to start one already-authenticated session."""
    start_request = greeter_protocol.new_request(
        greeter_protocol.ACTION_START_SESSION,
        {
            "command": command,
            "desktop_names": desktop_names,
        },
    )
    start_answer = app.send_recv_answer(start_request)

    return bool(start_answer.get("ok"))


def handle_conversation_answer(app: Any, answer: dict[str, Any]) -> str:
    """Advance the current greeter-side conversation state from one reply."""
    if not answer.get("ok"):
        error_message = str(answer.get("error", {}).get("message", "")) or _("Authentication failed.")
        app.clear_conversation_state()
        app.set_status(error_message, error=True)
        return "failed"

    payload = answer.get("payload", {})
    state = str(payload.get("state", ""))

    if state == "pending":
        message = payload.get("message", {})
        style = str(message.get("style", ""))
        text = str(message.get("text", ""))

        if style not in {"secret", "visible", "info", "error"}:
            logger.warning("unsupported auth conversation step: %s", answer)
            app.clear_conversation_state()
            app.set_status(_("Authentication failed."), error=True)
            return "failed"

        app.set_conversation_prompt(style, text)
        return "pending"

    if state == "ready":
        app.set_session_ready()
        return "ready"

    logger.warning("unexpected auth conversation state: %s", answer)
    app.clear_conversation_state()
    app.set_status(_("Authentication failed."), error=True)
    return "failed"


def on_cancel_clicked(app: Any) -> None:
    """Cancel the current staged auth flow and return to username entry."""
    if not (app.conversation_pending or app.session_ready or app.auth_username):
        return

    app.set_auth_state(True)
    try:
        if app.conversation_pending or app.session_ready:
            app.send_recv_answer(greeter_protocol.new_request(greeter_protocol.ACTION_CANCEL_SESSION, {}))
    finally:
        app.reset_auth_flow()


def on_login_clicked(app: Any) -> None:
    """Advance or start the greeter-side staged login flow."""
    if app.username_entry is None or app.password_entry is None:
        return

    if app.auth_in_progress:
        return

    if getattr(app, "session_ready", False):
        command, desktop_names = app.selected_session_data()
        app.set_auth_state(True)

        if app.start_selected_session(command, desktop_names):
            app.last_username = app.auth_username.strip()
            app.last_session_command = command
            app.username_entry.set_text("")
            app.set_status(_("Authentication accepted. Waiting for session..."))
            return

        app.set_auth_state(False)
        app.set_status(_("Unable to start session."), error=True)
        return

    if getattr(app, "conversation_pending", False):
        response = app.read_prompt_response()
        if response is None:
            return

        app.password_entry.set_text("")
        app.set_auth_state(True)

        try:
            answer = app.send_recv_answer(
                greeter_protocol.new_request(
                    greeter_protocol.ACTION_CONTINUE_SESSION,
                    {"response": response},
                )
            )
        finally:
            response.clear()

        app.set_auth_state(False)
        result = app.handle_conversation_answer(answer)

        if result in {"pending", "ready"}:
            return

        if hasattr(app.password_entry, "grab_focus"):
            app.password_entry.grab_focus()
        return

    username = app.username_entry.get_text()

    if len(username) == 0:
        app.set_status(_("Enter a username."), error=True)
        return

    if greeter_protocol.auth_field_is_too_long(username):
        app.set_status(
            _("Username must be %(limit)d bytes or less.")
            % {"limit": greeter_protocol.AUTH_FIELD_MAX_LENGTH},
            error=True,
        )
        return

    app.set_auth_state(True)
    app.auth_username = username

    create_request = greeter_protocol.new_request(
        greeter_protocol.ACTION_CREATE_SESSION,
        {"username": username},
    )
    create_answer = app.send_recv_answer(create_request)

    app.set_auth_state(False)
    result = app.handle_conversation_answer(create_answer)
    if result in {"pending", "ready"}:
        return

    if hasattr(app.password_entry, "grab_focus"):
        app.password_entry.grab_focus()
