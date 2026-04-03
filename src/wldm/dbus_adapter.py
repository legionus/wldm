#!/usr/bin/env python
# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026  Alexey Gladkov <legion@kernel.org>

import argparse
import os
import pwd
import socket

import wldm
import wldm.protocol

logger = wldm.logger


def adapter_ipc_fd() -> int:
    """Return the inherited daemon IPC fd for the adapter process.

    Returns:
        The connected socket fd passed down by the daemon.
    """
    socket_fd = os.environ.get("WLDM_SOCKET_FD", "").strip()
    if not socket_fd:
        raise RuntimeError("environ variable `WLDM_SOCKET_FD' not specified")

    fd = int(socket_fd)
    os.set_inheritable(fd, True)
    return fd


class SocketClient:
    def __init__(self, fd: int) -> None:
        self.sock = socket.socket(fileno=fd)

    def write_message(self, message: dict[str, object]) -> None:
        self.sock.sendall(wldm.protocol.encode_message(message))

    def read_message(self) -> dict[str, object] | None:
        return wldm.protocol.read_message_socket(self.sock)

    def close(self) -> None:
        self.sock.close()


def request_state(client: SocketClient) -> dict[str, object]:
    """Fetch the initial daemon state snapshot over the internal protocol.

    Args:
        client: Connected internal client transport.

    Returns:
        The decoded state snapshot payload returned by the daemon.
    """
    request = wldm.protocol.new_request(wldm.protocol.ACTION_GET_STATE, {})
    client.write_message(request)

    response = client.read_message()

    if response is None:
        raise RuntimeError("daemon closed the adapter channel")

    if not wldm.protocol.is_response(response, request):
        raise RuntimeError("daemon returned a malformed state response")

    if not response.get("ok", False):
        raise RuntimeError("daemon rejected the adapter state request")

    payload = response.get("payload", {})
    if not isinstance(payload, dict):
        raise RuntimeError("daemon returned a malformed state payload")

    return payload


def run_adapter(username: str, uid: int, gid: int, workdir: str) -> int:
    """Run the minimal adapter process on top of the inherited daemon channel.

    Args:
        username: Target user name for the adapter process.
        uid: Target user id for the adapter process.
        gid: Target group id for the adapter process.
        workdir: Working directory used after dropping privileges.

    Returns:
        A shell-style process exit status.
    """
    client = SocketClient(adapter_ipc_fd())

    try:
        wldm.drop_privileges(username, uid, gid, workdir)
        request_state(client)

        while True:
            message = client.read_message()

            if message is None:
                return wldm.EX_SUCCESS

            if wldm.protocol.is_event(message, name=wldm.protocol.EVENT_STATE_CHANGED):
                continue

            if wldm.protocol.is_event(message, name=wldm.protocol.EVENT_SESSION_STARTING):
                continue

            if wldm.protocol.is_event(message, name=wldm.protocol.EVENT_SESSION_FINISHED):
                continue

            logger.debug("ignoring unexpected adapter message: %r", message)

    finally:
        client.close()


def cmd_main(parser: argparse.Namespace) -> int:
    try:
        pw = pwd.getpwnam(parser.username)

    except KeyError:
        logger.critical("User '%s' not found.", parser.username)
        return wldm.EX_FAILURE

    try:
        return run_adapter(pw.pw_name, pw.pw_uid, pw.pw_gid, pw.pw_dir)

    except RuntimeError as e:
        logger.critical("[!] %s", e)
        return wldm.EX_FAILURE

    except Exception:
        logger.exception("unexpected dbus adapter failure")
        return wldm.EX_FAILURE
