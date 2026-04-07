#!/usr/bin/env python
# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026  Alexey Gladkov <legion@kernel.org>

import asyncio
import socket
import struct

from wldm.secret import SecretBytes


PROTOCOL_VERSION = 1

KIND_START = "start"
KIND_ANSWER = "answer"
KIND_CANCEL = "cancel"
KIND_PROMPT = "prompt"
KIND_READY = "ready"
KIND_FAILED = "failed"

FRAME_HEADER = struct.Struct("!I")
MAX_FRAME_BODY_LENGTH = 2048


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


def new_failed(message: str) -> dict[str, object]:
    """Build one worker failure message."""
    return {
        "v": PROTOCOL_VERSION,
        "kind": KIND_FAILED,
        "message": message,
    }


def _encode_blob(value: bytes | bytearray | str | SecretBytes) -> bytes:
    if isinstance(value, SecretBytes):
        data = value.as_bytes()
    elif isinstance(value, str):
        data = value.encode("utf-8")
    else:
        data = bytes(value)

    return FRAME_HEADER.pack(len(data)) + data


def _decode_blob(payload: memoryview, offset: int) -> tuple[bytes, int]:
    if offset + FRAME_HEADER.size > len(payload):
        raise ProtocolError("truncated length-prefixed field", payload.tobytes())

    size = FRAME_HEADER.unpack(payload[offset:offset + FRAME_HEADER.size])[0]
    offset += FRAME_HEADER.size

    if offset + size > len(payload):
        raise ProtocolError("truncated protocol field data", payload.tobytes())

    return payload[offset:offset + size].tobytes(), offset + size


def _encode_text(value: str) -> bytes:
    return _encode_blob(value)


def _decode_text(payload: memoryview, offset: int) -> tuple[str, int]:
    data, offset = _decode_blob(payload, offset)

    try:
        return data.decode("utf-8"), offset

    except UnicodeDecodeError as exc:
        raise ProtocolError("invalid utf-8 in protocol field", payload.tobytes()) from exc


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
        body.extend(_encode_text(str(message.get("message", ""))))

    else:
        raise ProtocolError(f"unsupported PAM worker message kind: {kind}")

    return FRAME_HEADER.pack(len(body)) + bytes(body)


def decode_message(frame: bytes) -> dict[str, object]:
    """Decode one PAM worker protocol frame."""
    if len(frame) < FRAME_HEADER.size:
        raise ProtocolError("truncated protocol frame", frame)

    body_size = FRAME_HEADER.unpack(frame[:FRAME_HEADER.size])[0]

    if body_size > MAX_FRAME_BODY_LENGTH:
        raise ProtocolError("protocol frame body is too large", frame)

    if len(frame) - FRAME_HEADER.size != body_size:
        raise ProtocolError("protocol frame length mismatch", frame)

    payload = memoryview(frame[FRAME_HEADER.size:])
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
        message, offset = _decode_text(payload, offset)
        decoded["message"] = message

    else:
        raise ProtocolError(f"unsupported PAM worker message kind: {kind}", frame)

    if offset != len(payload):
        raise ProtocolError("trailing bytes after protocol message", frame)

    return decoded


async def read_message_async(reader: asyncio.StreamReader) -> dict[str, object] | None:
    """Read one PAM worker message from an asyncio stream."""
    try:
        header = await reader.readexactly(FRAME_HEADER.size)

    except asyncio.IncompleteReadError as exc:
        if not exc.partial:
            return None
        raise ProtocolError("truncated protocol frame", exc.partial) from exc

    body_size = FRAME_HEADER.unpack(header)[0]
    if body_size > MAX_FRAME_BODY_LENGTH:
        raise ProtocolError("protocol frame body is too large", header)

    try:
        body = await reader.readexactly(body_size)

    except asyncio.IncompleteReadError as exc:
        raise ProtocolError("truncated protocol frame body", header + exc.partial) from exc

    return decode_message(header + body)


def read_message_socket(sock: socket.socket) -> dict[str, object] | None:
    """Read one PAM worker message from a blocking socket."""
    header = sock.recv(FRAME_HEADER.size)
    if not header:
        return None
    if len(header) != FRAME_HEADER.size:
        raise ProtocolError("truncated protocol frame", header)

    body_size = FRAME_HEADER.unpack(header)[0]
    if body_size > MAX_FRAME_BODY_LENGTH:
        raise ProtocolError("protocol frame body is too large", header)

    body = bytearray()
    while len(body) < body_size:
        chunk = sock.recv(body_size - len(body))
        if not chunk:
            raise ProtocolError("truncated protocol frame body", header + bytes(body))
        body.extend(chunk)

    return decode_message(header + bytes(body))
