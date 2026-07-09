#!/usr/bin/env python
# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026  Alexey Gladkov <legion@kernel.org>

import asyncio
import socket

from wldm.protocol import framing
from wldm.secret import SecretBytes


PROTOCOL_VERSION = 1

KIND_START = "start"
KIND_ANSWER = "answer"
KIND_CANCEL = "cancel"
KIND_PROMPT = "prompt"
KIND_READY = "ready"
KIND_FAILED = "failed"

FRAME_HEADER = framing.FRAME_HEADER
MAX_FRAME_BODY_LENGTH = framing.MAX_FRAME_BODY_LENGTH


class ProtocolError(ValueError):
    """Raised when a PAM worker message is malformed."""

    def __init__(self, message: str, raw: bytes = b"") -> None:
        super().__init__(message)
        self.raw = raw


def new_start(service: str, username: str, tty: str) -> dict[str, object]:
    """Build a worker start message."""
    return {
        "v": PROTOCOL_VERSION,
        "kind": KIND_START,
        "service": service,
        "username": username,
        "tty": tty,
    }


def new_answer(response: bytes | SecretBytes) -> dict[str, object]:
    """Build one worker answer message."""
    return {
        "v": PROTOCOL_VERSION,
        "kind": KIND_ANSWER,
        "response": response,
    }


def new_cancel() -> dict[str, object]:
    """Build one worker cancel message."""
    return {"v": PROTOCOL_VERSION, "kind": KIND_CANCEL}


def new_prompt(style: str, text: str) -> dict[str, object]:
    """Build one worker prompt message."""
    return {
        "v": PROTOCOL_VERSION,
        "kind": KIND_PROMPT,
        "style": style,
        "text": text,
    }


def new_ready() -> dict[str, object]:
    """Build one worker ready message."""
    return {"v": PROTOCOL_VERSION, "kind": KIND_READY}


def new_failed(code: str, message: str) -> dict[str, object]:
    """Build one worker failure message."""
    return {
        "v": PROTOCOL_VERSION,
        "kind": KIND_FAILED,
        "code": code,
        "message": message,
    }


_encode_blob = framing.encode_blob
_encode_text = framing.encode_text


def _decode_blob(payload: memoryview, offset: int) -> tuple[bytes, int]:
    return framing.decode_blob(payload, offset, ProtocolError)


def _decode_text(payload: memoryview, offset: int) -> tuple[str, int]:
    return framing.decode_text(payload, offset, ProtocolError)


def encode_message(message: dict[str, object]) -> bytes:
    """Encode one PAM worker protocol message."""
    body = bytearray()
    body.append(PROTOCOL_VERSION)

    kind = str(message.get("kind", ""))
    body.extend(_encode_text(kind))

    if kind == KIND_START:
        body.extend(_encode_text(str(message.get("service", ""))))
        body.extend(_encode_text(str(message.get("username", ""))))
        body.extend(_encode_text(str(message.get("tty", ""))))

    elif kind == KIND_ANSWER:
        response = message.get("response", b"")
        if not isinstance(response, (bytes, bytearray, str, SecretBytes)):
            raise ProtocolError(f"unsupported PAM worker answer payload: {type(response).__name__}")
        body.extend(_encode_blob(response))

    elif kind == KIND_CANCEL:
        pass

    elif kind == KIND_PROMPT:
        body.extend(_encode_text(str(message.get("style", ""))))
        body.extend(_encode_text(str(message.get("text", ""))))

    elif kind == KIND_READY:
        pass

    elif kind == KIND_FAILED:
        body.extend(_encode_text(str(message.get("code", ""))))
        body.extend(_encode_text(str(message.get("message", ""))))

    else:
        raise ProtocolError(f"unsupported PAM worker message kind: {kind}")

    return framing.encode_frame(body, MAX_FRAME_BODY_LENGTH, ProtocolError)


def decode_message(frame: bytes) -> dict[str, object]:
    """Decode one PAM worker protocol frame."""
    payload = framing.frame_payload(frame, MAX_FRAME_BODY_LENGTH, ProtocolError)
    if not payload:
        raise ProtocolError("missing protocol body", frame)

    version = payload[0]
    if version != PROTOCOL_VERSION:
        raise ProtocolError(f"unsupported protocol version: {version}", frame)

    offset = 1
    kind, offset = _decode_text(payload, offset)
    decoded: dict[str, object] = {"v": version, "kind": kind}

    if kind == KIND_START:
        service, offset = _decode_text(payload, offset)
        username, offset = _decode_text(payload, offset)
        tty, offset = _decode_text(payload, offset)
        decoded.update({"service": service, "username": username, "tty": tty})

    elif kind == KIND_ANSWER:
        response, offset = _decode_blob(payload, offset)
        decoded["response"] = SecretBytes(response)

    elif kind == KIND_CANCEL:
        pass

    elif kind == KIND_PROMPT:
        style, offset = _decode_text(payload, offset)
        text, offset = _decode_text(payload, offset)
        decoded.update({"style": style, "text": text})

    elif kind == KIND_READY:
        pass

    elif kind == KIND_FAILED:
        code, offset = _decode_text(payload, offset)
        message, offset = _decode_text(payload, offset)
        decoded["code"] = code
        decoded["message"] = message

    else:
        raise ProtocolError(f"unsupported PAM worker message kind: {kind}", frame)

    if offset != len(payload):
        raise ProtocolError("trailing bytes after protocol message", frame)

    return decoded


async def read_message_async(reader: asyncio.StreamReader) -> dict[str, object] | None:
    """Read one PAM worker message from an asyncio stream."""
    frame = await framing.read_frame_async(
        reader,
        MAX_FRAME_BODY_LENGTH,
        ProtocolError,
        "truncated protocol frame",
    )
    if frame is None:
        return None

    return decode_message(frame)


def read_message_socket(sock: socket.socket) -> dict[str, object] | None:
    """Read one PAM worker message from a blocking socket."""
    frame = framing.read_frame_socket(sock, MAX_FRAME_BODY_LENGTH, ProtocolError, "truncated protocol frame")
    if frame is None:
        return None

    return decode_message(frame)
