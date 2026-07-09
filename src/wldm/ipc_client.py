#!/usr/bin/env python
# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026  Alexey Gladkov <legion@kernel.org>

"""Blocking client for the daemon's internal greeter protocol channel."""

import select
import socket

from typing import Any, Dict

import wldm
import wldm.protocol.greeter as greeter_protocol


class SocketClient:
    """Send and receive greeter protocol messages on an inherited socket."""

    def __init__(self, fd: int) -> None:
        self.sock = socket.socket(fileno=fd)

    @classmethod
    def from_inherited_env(cls, env_name: str = "WLDM_SOCKET_FD") -> "SocketClient":
        """Create a client from one inherited socket fd environment variable."""
        return cls(wldm.inherited_socket_fd(env_name))

    def write_message(self, message: Dict[str, Any]) -> None:
        """Write one protocol message."""
        self.sock.sendall(greeter_protocol.encode_message(message))

    def read_message(self) -> Dict[str, Any] | None:
        """Read one protocol message."""
        return greeter_protocol.read_message_socket(self.sock)

    def can_read(self) -> bool:
        """Return whether the socket can be read without blocking."""
        readable, _, _ = select.select([self.sock], [], [], 0.0)
        return self.sock in readable

    def close(self) -> None:
        """Close the client socket."""
        self.sock.close()
