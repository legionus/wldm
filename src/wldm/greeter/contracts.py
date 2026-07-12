#!/usr/bin/env python
# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026  Alexey Gladkov <legion@kernel.org>

from typing import Any, Protocol

import wldm
import wldm.secret


class GreeterEntry(Protocol):
    """Text entry methods required by the shared greeter flow."""

    def get_text(self) -> str:
        """Return the current entry text."""

    def set_text(self, text: str) -> None:
        """Replace the current entry text."""

    def grab_focus(self) -> None:
        """Move input focus to the entry."""


class GreeterTransport(Protocol):
    """Daemon IPC methods required by the shared greeter client code."""

    def can_read(self) -> bool:
        """Return whether a daemon message can be read without blocking."""

    def read_message(self) -> dict[str, Any] | None:
        """Read one decoded daemon protocol message."""

    def write_message(self, message: dict[str, Any]) -> None:
        """Write one encoded daemon protocol message."""

    def close(self) -> None:
        """Close the daemon IPC transport."""


class GreeterClientApp(Protocol):
    """Frontend callbacks required by the daemon IPC event helpers."""

    client: GreeterTransport
    username_entry: GreeterEntry | None
    password_entry: GreeterEntry | None
    last_username: str
    last_session_command: str

    def set_status(self, message: str, error: bool = False) -> None:
        """Show one status message in the frontend."""

    def set_auth_state(self, busy: bool) -> None:
        """Switch the frontend into or out of a busy auth state."""

    def clear_conversation_state(self) -> None:
        """Forget the current authentication conversation state."""

    def clear_username_selection(self) -> None:
        """Move the username cursor to the end of the entry text."""

    def refresh_sessions(self, username: str = "", preferred_command: str = "") -> None:
        """Reload the visible session list for one username."""

    def save_last_session_state(self) -> None:
        """Persist the current remembered username and session choice."""

    def handle_connection_lost(self) -> None:
        """Handle daemon IPC loss."""

    def log_protocol_error(self, context: str, raw: bytes, error: Exception) -> None:
        """Log one malformed daemon protocol message."""

    def handle_event(self, event: dict[str, Any]) -> None:
        """Handle one asynchronous daemon event."""

    def on_quit(self) -> None:
        """Request frontend shutdown."""

    def reexec_self(self) -> None:
        """Replace the frontend process image."""


class GreeterAuthApp(Protocol):
    """Frontend callbacks required by the shared authentication flow."""

    username_entry: GreeterEntry | None
    password_entry: GreeterEntry | None
    auth_in_progress: bool
    auth_username: str
    conversation_pending: bool
    conversation_prompt_style: str
    conversation_prompt_text: str
    session_ready: bool
    last_username: str
    last_session_command: str

    def set_status(self, message: str, error: bool = False) -> None:
        """Show one status message in the frontend."""

    def set_auth_state(self, busy: bool) -> None:
        """Switch the frontend into or out of a busy auth state."""

    def clear_conversation_state(self) -> None:
        """Forget the current authentication conversation state."""

    def set_conversation_prompt(self, style: str, text: str) -> None:
        """Show one pending authentication prompt."""

    def set_session_ready(self) -> None:
        """Move the frontend to the post-auth session selection stage."""

    def reset_auth_flow(self) -> None:
        """Return the frontend to the initial username entry stage."""

    def refresh_sessions(self, username: str = "", preferred_command: str = "") -> None:
        """Reload the visible session list for one username."""

    def send_recv_answer(self, data: dict[str, Any]) -> dict[str, Any]:
        """Send one daemon request and return the matching response."""

    def read_prompt_response(self) -> wldm.secret.SecretBytes | None:
        """Read one answer for the current pending authentication prompt."""

    def start_selected_session(self,
                               command: str,
                               desktop_names: list[str],
                               name: str = "",
                               icon: str = "",
                               desktop_file: str = "") -> bool:
        """Ask the daemon to start one authenticated user session."""

    def selected_session_data(self) -> tuple[str, list[str], str, str, str]:
        """Return the selected session command and desktop metadata."""

    def handle_conversation_answer(self, answer: dict[str, Any]) -> str:
        """Advance the authentication conversation from one daemon response."""
