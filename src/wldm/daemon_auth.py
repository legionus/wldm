#!/usr/bin/env python
# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026  Alexey Gladkov <legion@kernel.org>

import asyncio
import os
import socket
from contextlib import suppress
from dataclasses import dataclass
from typing import Any
from asyncio.subprocess import Process as AsyncProcess

import wldm
import wldm.greeter_protocol as greeter_protocol
import wldm.pam_worker_protocol as pam_worker_protocol
import wldm.secret

logger = wldm.logger


@dataclass
class AuthSessionState:
    """Track one greeter-side configuring session backed by a PAM worker."""

    service: str
    username: str
    tty: str
    proc: AsyncProcess
    reader: asyncio.StreamReader
    writer: asyncio.StreamWriter
    ready: bool = False


def tty_device_path(tty_num: int) -> str:
    """Return the TTY device path exposed to PAM for greeter auth."""
    if tty_num <= 0:
        return ""

    return f"/dev/tty{tty_num}"


async def terminate_process(proc: AsyncProcess,
                            name: str,
                            timeout: float = 5.0) -> None:
    """Terminate one direct child process without process-group semantics."""
    if proc.returncode is not None:
        return

    logger.info("terminate %s (pid=%d)", name, proc.pid)
    proc.terminate()

    try:
        await asyncio.wait_for(proc.wait(), timeout=timeout)
        return
    except asyncio.TimeoutError:
        logger.critical("%s (pid=%d) did not stop after SIGTERM, sending SIGKILL", name, proc.pid)

    proc.kill()
    await proc.wait()


async def stop_auth_session(auth_session: AuthSessionState,
                            *,
                            send_cancel: bool = False) -> None:
    """Stop one PAM worker session and release its IPC channel."""
    if send_cancel:
        try:
            auth_session.writer.write(pam_worker_protocol.encode_message(pam_worker_protocol.new_cancel()))
            await auth_session.writer.drain()
        except Exception as e:
            logger.debug("unable to send cancel to pam-worker pid=%d service=%s user=%s tty=%s: %s",
                         auth_session.proc.pid, auth_session.service,
                         auth_session.username, auth_session.tty, e)

    auth_session.writer.close()

    with suppress(Exception):
        await auth_session.writer.wait_closed()

    await terminate_process(auth_session.proc, "pam-worker")


async def read_auth_worker_message(auth_session: AuthSessionState) -> dict[str, object] | None:
    """Read one message from the PAM worker channel."""
    message = await pam_worker_protocol.read_message_async(auth_session.reader)
    if message is not None:
        logger.debug("pam-worker pid=%d service=%s user=%s tty=%s -> %s",
                     auth_session.proc.pid, auth_session.service,
                     auth_session.username, auth_session.tty, message["kind"])
    return message


def conversation_response_from_worker(req: dict[str, Any],
                                      message: dict[str, object]) -> dict[str, Any]:
    """Map one PAM worker message into a greeter protocol response."""
    kind = str(message.get("kind", ""))

    if kind == pam_worker_protocol.KIND_PROMPT:
        return greeter_protocol.new_conversation_response(
            req,
            "pending",
            style=str(message.get("style", "")),
            text=str(message.get("text", "")),
        )

    if kind == pam_worker_protocol.KIND_READY:
        return greeter_protocol.new_conversation_response(req, "ready")

    if kind == pam_worker_protocol.KIND_FAILED:
        return greeter_protocol.new_error(
            req,
            str(message.get("code", "auth_failed")),
            str(message.get("message", "Authentication failed")),
        )

    raise RuntimeError(f"unexpected PAM worker message: {message!r}")


async def start_auth_session(internal_command: list[str],
                             tty: str,
                             username: str) -> tuple[AuthSessionState, dict[str, object] | None]:
    """Start one PAM worker for a greeter-side configuring session."""
    service = "login"
    daemon_sock, child_sock = socket.socketpair()

    proc = await asyncio.create_subprocess_exec(
        *internal_command,
        "pam-worker",
        env=dict(os.environ, WLDM_SOCKET_FD=str(child_sock.fileno())),
        pass_fds=(child_sock.fileno(),),
    )
    child_sock.close()

    reader, writer = await asyncio.open_connection(sock=daemon_sock)
    auth_session = AuthSessionState(
        service=service,
        username=username,
        tty=tty,
        proc=proc,
        reader=reader,
        writer=writer,
    )

    writer.write(
        pam_worker_protocol.encode_message(
            pam_worker_protocol.new_start(service, username, tty)
        )
    )
    await writer.drain()

    logger.info("start pam-worker (pid=%d) service=%s user=%s tty=%s",
                proc.pid, service, username, tty or "<none>")

    return auth_session, await read_auth_worker_message(auth_session)


async def continue_auth_session(auth_session: AuthSessionState,
                                response: wldm.secret.SecretBytes) -> dict[str, object] | None:
    """Send one prompt reply to the PAM worker and wait for the next result."""
    try:
        logger.debug("send prompt reply to pam-worker pid=%d service=%s user=%s tty=%s (%d bytes)",
                     auth_session.proc.pid, auth_session.service,
                     auth_session.username, auth_session.tty,
                     len(response.as_bytes()))

        auth_session.writer.write(
            pam_worker_protocol.encode_message(
                pam_worker_protocol.new_answer(response)
            )
        )
        await auth_session.writer.drain()
    finally:
        response.clear()

    return await read_auth_worker_message(auth_session)
