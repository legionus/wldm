#!/usr/bin/env python
# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026  Alexey Gladkov <legion@kernel.org>

import argparse
import ctypes
import os
import socket

from ctypes import POINTER, byref, c_char, c_char_p, c_void_p, cast, sizeof
from typing import Any

import wldm
import wldm._pam_ffi as ffi
import wldm.pam
import wldm.pam_worker_protocol as worker_protocol
from wldm._libc import calloc, free
from wldm.secret import SecretBytes

logger = wldm.logger

_brokers: dict[int, "PromptBroker"] = {}


class ConversationError(RuntimeError):
    """Raised when the PAM worker conversation is cancelled or malformed."""


class PromptBroker:
    """Bridge blocking PAM callbacks to daemon-driven prompt replies."""

    def __init__(self, sock: Any, *, service: str, username: str, tty: str) -> None:
        self.sock = sock
        self.service = service
        self.username = username
        self.tty = tty

    def ask(self, style: str, text: str) -> bytes | None:
        """Send one prompt to the daemon and wait for the matching reply."""
        logger.debug("pam-worker service=%s user=%s tty=%s prompt style=%s text=%r",
                     self.service, self.username, self.tty or "<none>", style, text)

        try:
            self.sock.sendall(worker_protocol.encode_message(worker_protocol.new_prompt(style, text)))

            message = worker_protocol.read_message_socket(self.sock)

        except OSError as exc:
            raise ConversationError("daemon closed PAM worker channel") from exc

        if message is None:
            raise ConversationError("daemon closed PAM worker channel")

        if message["kind"] == worker_protocol.KIND_CANCEL:
            logger.info("pam-worker service=%s user=%s tty=%s cancelled by daemon",
                        self.service, self.username, self.tty or "<none>")

            return None

        if message["kind"] != worker_protocol.KIND_ANSWER:
            raise ConversationError(f"unexpected PAM worker reply: {message!r}")

        response = message["response"]

        if isinstance(response, SecretBytes):
            return response.as_bytes()

        if isinstance(response, (bytes, bytearray, memoryview)):
            return bytes(response)

        raise ConversationError(f"unexpected PAM worker answer payload: {type(response).__name__}")


def _register_broker(broker: PromptBroker) -> int:
    broker_id = id(broker)
    _brokers[broker_id] = broker
    return broker_id


def _unregister_broker(broker_id: int) -> None:
    _brokers.pop(broker_id, None)


def _broker(broker_id: int) -> PromptBroker:
    broker = _brokers.get(broker_id)
    if broker is None:
        raise ConversationError(f"unknown PAM worker broker: {broker_id}")
    return broker


def _prompt_style(style: int) -> str:
    """Translate one PAM message style into greeter prompt style."""
    if style == ffi.PAM_PROMPT_ECHO_OFF:
        return "secret"
    if style == ffi.PAM_PROMPT_ECHO_ON:
        return "visible"
    if style == ffi.PAM_TEXT_INFO:
        return "info"
    if style == ffi.PAM_ERROR_MSG:
        return "error"
    raise ConversationError(f"unsupported PAM message style: {style}")


def _free_response_array(arr: Any, filled: int) -> None:
    """Free a partially filled PAM response array."""
    for index in range(filled):
        if arr[index].resp:
            free(cast(arr[index].resp, c_void_p))

    free(cast(arr, c_void_p))


def _copy_response_bytes(data: bytes) -> Any:
    """Allocate and copy one native PAM response string."""
    resp = calloc(len(data) + 1, sizeof(c_char))
    if not resp:
        return None

    ctypes.memmove(resp, c_char_p(data), len(data))
    return resp


def _resolve_broker_from_appdata(appdata_ptr: Any) -> PromptBroker:
    """Resolve the registered prompt broker from one PAM appdata pointer."""
    if not appdata_ptr:
        raise ConversationError("missing PAM worker broker pointer")

    broker_id = cast(appdata_ptr, c_void_p).value

    if broker_id is None:
        raise ConversationError("missing PAM worker broker id")

    return _broker(broker_id)


def user_facing_error(stage: str, rc: int) -> str:
    """Translate one PAM failure into a greeter-facing error message."""
    if stage == "auth":
        if rc in {ffi.PAM_AUTH_ERR, ffi.PAM_USER_UNKNOWN, ffi.PAM_MAXTRIES}:
            return "Authentication failed."

        if rc in {ffi.PAM_CRED_INSUFFICIENT, ffi.PAM_ABORT}:
            return "Authentication service unavailable."

    elif stage == "acct":
        if rc == ffi.PAM_NEW_AUTHTOK_REQD:
            return "Password change required."

        if rc == ffi.PAM_ACCT_EXPIRED:
            return "Account expired."

        if rc in {ffi.PAM_AUTH_ERR, ffi.PAM_USER_UNKNOWN}:
            return "Authentication failed."

        if rc in {ffi.PAM_CRED_INSUFFICIENT, ffi.PAM_ABORT, ffi.PAM_AUTHTOK_LOCK_BUSY, ffi.PAM_AUTHTOK_DISABLE_AGING}:
            return "Authentication service unavailable."

    return "Authentication failed."


def failure_code(stage: str, rc: int) -> str:
    """Translate one PAM failure into a machine-readable worker error code."""
    if stage == "auth" and rc == ffi.PAM_AUTH_ERR:
        return "auth_retryable"

    return "auth_failed"


def _send_auth_failure(sock: Any, *, service: str, username: str, tty: str,
                       code: str, message: str, detail: str) -> int:
    """Log and send one PAM authentication failure to the daemon."""
    logger.warning("pam-worker authentication failed service=%s user=%s tty=%s: %s",
                   service, username, tty or "<none>", detail)

    sock.sendall(worker_protocol.encode_message(worker_protocol.new_failed(code, message)))
    return wldm.EX_FAILURE


def _fill_response_slot(arr: Any, index: int, answer: bytes) -> None:
    """Copy one prompt reply into the native PAM response array."""
    resp = _copy_response_bytes(answer)
    if not resp:
        raise ConversationError("unable to allocate PAM response buffer")

    arr[index].resp = resp


def _process_conversation_message(arr: Any, index: int, message: Any, broker: PromptBroker) -> bool:
    """Handle one PAM conversation message and fill one response slot.

    Returns:
        ``True`` when the callback should continue with the next message and
        ``False`` when the conversation should abort with ``PAM_CONV_ERR``.
    """
    style = _prompt_style(message.msg_style)
    text = message.msg.decode(errors="replace") if message.msg else ""

    try:
        answer = broker.ask(style, text)

    except Exception as exc:
        raise ConversationError(f"prompt callback failed: {exc}") from exc

    if answer is None:
        return False

    arr[index].resp_retcode = 0

    if style in {"info", "error"}:
        arr[index].resp = None
        return True

    _fill_response_slot(arr, index, answer)

    return True


def _conversation_conv(n_messages: int,
                       messages: Any,
                       response: Any,
                       appdata_ptr: Any) -> int:
    """Run one blocking PAM conversation callback batch."""
    try:
        broker = _resolve_broker_from_appdata(appdata_ptr)

    except ConversationError as e:
        logger.critical("PAM worker callback failed to resolve broker: %s", e)
        return ffi.PAM_CONV_ERR

    resp_ptr = calloc(n_messages, sizeof(ffi.PamResponse))

    if not resp_ptr:
        logger.critical("PAM worker callback could not allocate response array")
        return ffi.PAM_CONV_ERR

    arr = cast(resp_ptr, POINTER(ffi.PamResponse))

    try:
        for index in range(n_messages):
            if not _process_conversation_message(arr, index, messages[index].contents, broker):
                _free_response_array(arr, index)
                return ffi.PAM_CONV_ERR

    except ConversationError as e:
        logger.critical("PAM worker callback failed: %s", e)
        _free_response_array(arr, n_messages)
        return ffi.PAM_CONV_ERR

    response[0] = arr
    return ffi.PAM_SUCCESS


def inherited_socket_fd() -> int:
    """Return the daemon-provided PAM worker socket fd."""
    value = os.environ.get("WLDM_SOCKET_FD", "")

    try:
        fd = int(value)
    except ValueError as exc:
        raise RuntimeError("invalid or missing WLDM_SOCKET_FD") from exc

    if fd < 0:
        raise RuntimeError("WLDM_SOCKET_FD must be non-negative")

    return fd


def run_auth_session(sock: Any, service: str, username: str, tty: str) -> int:
    """Run one blocking PAM authentication session and report prompts upstream."""
    logger.info("pam-worker start service=%s user=%s tty=%s",
                service, username, tty or "<none>")

    broker = PromptBroker(sock, service=service, username=username, tty=tty)
    broker_id = _register_broker(broker)
    conv = ffi.PamConv(ffi.PAM_CONV_FUNC(_conversation_conv), c_void_p(broker_id))
    pamh = ffi.pam_handle_t()

    try:
        rc = ffi.libpam.pam_start(service.encode(), username.encode(), byref(conv), byref(pamh))

        if rc != ffi.PAM_SUCCESS:
            raise RuntimeError(f"pam_start failed: {rc} ({wldm.pam.pam_error_str(None, rc)})")

        if tty:
            wldm.pam.set_pam_item(pamh, ffi.PAM_TTY, tty)

        rc = ffi.libpam.pam_authenticate(pamh, 0)

        if rc != ffi.PAM_SUCCESS:
            return _send_auth_failure(sock, service=service, username=username,
                                      tty=tty, code=failure_code("auth", rc),
                                      message=user_facing_error("auth", rc),
                                      detail=f"pam_authenticate failed: {rc} ({wldm.pam.pam_error_str(pamh, rc)})")

        rc = ffi.libpam.pam_acct_mgmt(pamh, 0)

        if rc != ffi.PAM_SUCCESS:
            return _send_auth_failure(sock, service=service, username=username,
                                      tty=tty, code=failure_code("acct", rc),
                                      message=user_facing_error("acct", rc),
                                      detail=f"pam_acct_mgmt failed: {rc} ({wldm.pam.pam_error_str(pamh, rc)})")

        logger.info("pam-worker authentication ready service=%s user=%s tty=%s",
                    service, username, tty or "<none>")

        sock.sendall(worker_protocol.encode_message(worker_protocol.new_ready()))
        return wldm.EX_SUCCESS

    except ConversationError as e:
        logger.warning("pam-worker conversation aborted service=%s user=%s tty=%s: %s",
                       service, username, tty or "<none>", e)

        return wldm.EX_FAILURE

    except Exception as e:
        return _send_auth_failure(sock, service=service, username=username,
                                  tty=tty, code="auth_failed",
                                  message=str(e),
                                  detail=str(e))

    finally:
        wldm.pam.end_pam(pamh)
        _unregister_broker(broker_id)


def cmd_main(_parser: argparse.Namespace) -> int:
    """Run the PAM worker subcommand."""
    fd = inherited_socket_fd()
    sock = socket.socket(fileno=fd)

    try:
        message = worker_protocol.read_message_socket(sock)
        if message is None:
            raise RuntimeError("daemon closed PAM worker channel before start")

        if message["kind"] != worker_protocol.KIND_START:
            raise RuntimeError(f"expected start message, got {message!r}")

        return run_auth_session(
            sock,
            str(message["service"]),
            str(message["username"]),
            str(message["tty"]),
        )
    finally:
        sock.close()
