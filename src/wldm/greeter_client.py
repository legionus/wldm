#!/usr/bin/env python
# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026  Alexey Gladkov <legion@kernel.org>

import gettext
from typing import Any

import wldm
import wldm.greeter_protocol as greeter_protocol

_ = gettext.gettext
logger = wldm.logger


def log_protocol_error(_app: Any, context: str, raw: bytes, error: Exception) -> None:
    """Log one malformed greeter protocol message."""
    logger.critical("%s: %s; raw=%r", context, error, raw)


def handle_connection_lost(app: Any) -> None:
    """Handle loss of the daemon IPC channel."""
    app.set_status(_("Connection to daemon lost."), error=True)
    app.on_quit()


def poll_events(app: Any, lock: Any) -> None:
    """Poll and dispatch pending daemon events from the IPC channel."""
    connection_lost = False
    acquired = False

    try:
        acquired = lock.acquire(blocking=False)
        if not acquired:
            return

        while hasattr(app.client, "can_read") and app.client.can_read():
            try:
                message = app.client.read_message()

            except greeter_protocol.ProtocolError as e:
                app.log_protocol_error("bad greeter event message", e.raw, e)
                connection_lost = True
                break

            if message is None:
                connection_lost = True
                break

            if greeter_protocol.is_event(message):
                app.handle_event(message)
                continue

            logger.debug("unexpected protocol message while idle: %s", message)

    except Exception as e:
        logger.critical("unexpected polling error: %r", e)
        connection_lost = True

    finally:
        if acquired:
            lock.release()

    if connection_lost:
        app.handle_connection_lost()


def handle_event(app: Any, event: dict[str, Any]) -> None:
    """Handle one asynchronous daemon event."""
    if not greeter_protocol.is_event(event):
        return

    payload = event["payload"]
    event_name = event["event"]

    logger.debug("protocol event: %s", event)

    if event_name == greeter_protocol.EVENT_SESSION_STARTING:
        app.set_auth_state(True)
        app.set_status(_("Starting session..."))
        return

    if event_name == greeter_protocol.EVENT_SESSION_FINISHED:
        app.set_auth_state(False)
        app.clear_conversation_state()

        if not bool(payload.get("failed", False)):
            if app.username_entry is not None:
                current_username = app.username_entry.get_text().strip()
                if current_username:
                    app.last_username = current_username

            app.save_last_session_state()

        if app.username_entry is not None:
            app.username_entry.set_text(app.last_username)

            if hasattr(app.username_entry, "grab_focus"):
                app.username_entry.grab_focus()

            app.clear_username_selection()

        if app.password_entry is not None:
            app.password_entry.set_text("")

        app.refresh_sessions(app.last_username, preferred_command=app.last_session_command)

        status_message = str(payload.get("message", _("Session finished.")))
        app.set_status(status_message, error=bool(payload.get("failed", False)))


def send_recv_answer(app: Any, data: dict[str, Any], lock: Any) -> dict[str, Any]:
    """Send one request and wait for the matching daemon response."""
    answer: dict[str, Any] = {}
    connection_lost = False

    try:
        lock.acquire()
        app.client.write_message(data)

        while True:
            try:
                message = app.client.read_message()

            except greeter_protocol.ProtocolError as e:
                app.log_protocol_error("bad greeter response message", e.raw, e)
                connection_lost = True
                break

            if message is None:
                connection_lost = True
                break

            if greeter_protocol.is_event(message):
                app.handle_event(message)
                continue

            if greeter_protocol.is_response(message, data):
                answer = message
                break

    except Exception as e:
        logger.critical("unexpected error: %r", e)
        connection_lost = True

    finally:
        lock.release()

    if connection_lost:
        app.handle_connection_lost()

    return answer
