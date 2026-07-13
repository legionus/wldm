#!/usr/bin/env python
# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026  Alexey Gladkov <legion@kernel.org>

import asyncio
import socket
from contextlib import suppress
from typing import Any, Awaitable, Callable
from asyncio.subprocess import Process as AsyncProcess

import wldm
import wldm.protocol.greeter as greeter_protocol
import wldm.protocol.pam_worker as pam_worker_protocol
import wldm.process
import wldm.secret

logger = wldm.logger

AUTH_TIMEOUT_WARNING_MESSAGE = "Authentication time is running out. Hurry up..."
AUTH_TIMEOUT_EXPIRED_MESSAGE = "Authentication timed out."


class AuthSessionState:
    """Track one greeter-side configuring session backed by a PAM worker."""

    __slots__ = ("service", "username", "tty", "proc", "reader", "writer", "ready")

    def __init__(self,
                 service: str,
                 username: str,
                 tty: str,
                 proc: AsyncProcess,
                 reader: asyncio.StreamReader,
                 writer: asyncio.StreamWriter,
                 ready: bool = False) -> None:
        self.service = service
        self.username = username
        self.tty = tty
        self.proc = proc
        self.reader = reader
        self.writer = writer
        self.ready = ready


def tty_device_path(tty_num: int) -> str:
    """Return the TTY device path exposed to PAM for greeter auth."""
    if tty_num <= 0:
        return ""

    return f"/dev/tty{tty_num}"


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

    await wldm.process.terminate_process(auth_session.proc, "pam-worker")


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
        env=wldm.internal_helper_environ({
            "WLDM_ROLE": "pam-worker",
            "WLDM_SOCKET_FD": str(child_sock.fileno()),
        }),
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


async def run_auth_timeout(auth_session: AuthSessionState,
                           timeout: int,
                           is_current: Callable[[], bool],
                           send_auth_message: Callable[[str, str], Awaitable[None]],
                           expire: Callable[[], Awaitable[None]]) -> None:
    """Run one daemon-side idle timeout for a pending auth conversation."""
    warning_delay = max(0, timeout - max(1, timeout // 3))

    try:
        if warning_delay > 0:
            await asyncio.sleep(warning_delay)

            if not is_current():
                return

            await send_auth_message("warning", AUTH_TIMEOUT_WARNING_MESSAGE)

        await asyncio.sleep(max(0, timeout - warning_delay))

        if not is_current():
            return

        logger.info("pam-worker pid=%d service=%s user=%s tty=%s timed out after %d seconds",
                    auth_session.proc.pid, auth_session.service,
                    auth_session.username, auth_session.tty or "<none>", timeout)

        await expire()
        await send_auth_message("error", AUTH_TIMEOUT_EXPIRED_MESSAGE)

    except asyncio.CancelledError:
        pass
