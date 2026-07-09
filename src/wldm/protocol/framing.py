#!/usr/bin/env python
# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026  Alexey Gladkov <legion@kernel.org>

"""Shared helpers for WLDM length-prefixed protocol frames."""

import asyncio
import socket
import struct

from typing import Protocol

from wldm.secret import SecretBytes


FRAME_HEADER = struct.Struct("!I")
SIGNED_INT = struct.Struct("!i")
MAX_FRAME_BODY_LENGTH = 2048


class ErrorFactory(Protocol):
    """Build a protocol-specific exception."""

    def __call__(self, message: str, raw: bytes = b"") -> Exception:
        """Return an exception for one malformed frame or field."""


def encode_bool(value: bool) -> bytes:
    """Encode one boolean field."""
    return bytes([1 if value else 0])


def decode_bool(payload: memoryview, offset: int, error: ErrorFactory) -> tuple[bool, int]:
    """Decode one boolean field."""
    if offset >= len(payload):
        raise error("truncated boolean field", payload.tobytes())

    return payload[offset] != 0, offset + 1


def encode_text(value: str) -> bytes:
    """Encode one UTF-8 text field."""
    return encode_blob(value)


def decode_text(payload: memoryview, offset: int, error: ErrorFactory) -> tuple[str, int]:
    """Decode one UTF-8 text field."""
    data, offset = decode_blob(payload, offset, error)

    try:
        return data.decode("utf-8"), offset

    except UnicodeDecodeError as exc:
        raise error("invalid utf-8 in protocol field", payload.tobytes()) from exc


def encode_blob(value: bytes | bytearray | str | SecretBytes) -> bytes:
    """Encode one length-prefixed byte field."""
    if isinstance(value, SecretBytes):
        data = value.as_bytes()

    elif isinstance(value, str):
        data = value.encode("utf-8")

    else:
        data = bytes(value)

    return FRAME_HEADER.pack(len(data)) + data


def decode_blob(payload: memoryview, offset: int, error: ErrorFactory) -> tuple[bytes, int]:
    """Decode one length-prefixed byte field."""
    if offset + FRAME_HEADER.size > len(payload):
        raise error("truncated length-prefixed field", payload.tobytes())

    size = FRAME_HEADER.unpack(payload[offset:offset + FRAME_HEADER.size])[0]
    offset += FRAME_HEADER.size

    if offset + size > len(payload):
        raise error("truncated protocol field data", payload.tobytes())

    return payload[offset:offset + size].tobytes(), offset + size


def decode_secbytes(payload: memoryview, offset: int, error: ErrorFactory) -> tuple[SecretBytes, int]:
    """Decode one secret byte field."""
    data, offset = decode_blob(payload, offset, error)
    return SecretBytes(data), offset


def encode_signed_int(value: int) -> bytes:
    """Encode one signed integer field."""
    return SIGNED_INT.pack(value)


def decode_signed_int(payload: memoryview, offset: int, error: ErrorFactory) -> tuple[int, int]:
    """Decode one signed integer field."""
    if offset + SIGNED_INT.size > len(payload):
        raise error("truncated signed integer field", payload.tobytes())

    value = SIGNED_INT.unpack(payload[offset:offset + SIGNED_INT.size])[0]

    return value, offset + SIGNED_INT.size


def encode_frame(body: bytes | bytearray, max_body_length: int, error: ErrorFactory) -> bytes:
    """Encode one complete protocol frame."""
    if len(body) > max_body_length:
        raise error("protocol frame body is too large")

    return FRAME_HEADER.pack(len(body)) + bytes(body)


def frame_payload(frame: bytes, max_body_length: int, error: ErrorFactory) -> memoryview:
    """Return the body payload from one complete protocol frame."""
    if len(frame) < FRAME_HEADER.size:
        raise error("truncated protocol frame", frame)

    body_len = FRAME_HEADER.unpack(frame[:FRAME_HEADER.size])[0]
    body = frame[FRAME_HEADER.size:]

    if body_len > max_body_length:
        raise error("protocol frame body is too large", frame[:FRAME_HEADER.size])

    if len(body) != body_len:
        raise error("protocol frame length mismatch", frame)

    return memoryview(body)


async def read_frame_async(reader: asyncio.StreamReader,
                           max_body_length: int,
                           error: ErrorFactory,
                           truncated_header_message: str) -> bytes | None:
    """Read one complete protocol frame from an asyncio stream."""
    try:
        header = await reader.readexactly(FRAME_HEADER.size)

    except asyncio.IncompleteReadError as exc:
        if not exc.partial:
            return None

        raise error(truncated_header_message, bytes(exc.partial)) from exc

    body_len = FRAME_HEADER.unpack(header)[0]

    if body_len > max_body_length:
        raise error("protocol frame body is too large", header)

    try:
        body = await reader.readexactly(body_len)

    except asyncio.IncompleteReadError as exc:
        raise error("truncated protocol frame body", header + bytes(exc.partial)) from exc

    return header + body


def read_frame_socket(sock: socket.socket,
                      max_body_length: int,
                      error: ErrorFactory,
                      truncated_header_message: str | None = None) -> bytes | None:
    """Read one complete protocol frame from a blocking socket."""
    header = _recv_exact(sock, FRAME_HEADER.size)

    if header is None:
        return None

    if len(header) != FRAME_HEADER.size:
        if truncated_header_message is not None:
            raise error(truncated_header_message, header)
        return None

    body_len = FRAME_HEADER.unpack(header)[0]

    if body_len > max_body_length:
        raise error("protocol frame body is too large", header)

    body = _recv_exact(sock, body_len)

    if body is None or len(body) != body_len:
        raise error("truncated protocol frame body", header + (body or b""))

    return header + body


def _recv_exact(sock: socket.socket, size: int) -> bytes | None:
    chunks = bytearray()

    while len(chunks) < size:
        chunk = sock.recv(size - len(chunks))

        if not chunk:
            if not chunks:
                return None
            return bytes(chunks)

        chunks.extend(chunk)

    return bytes(chunks)
