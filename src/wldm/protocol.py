#!/usr/bin/env python
# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026  Alexey Gladkov <legion@kernel.org>

import asyncio
import socket
import struct
import uuid

from typing import Any, Dict

from wldm.secret import SecretBytes


PROTOCOL_VERSION = 1

ACTION_AUTH = "auth"
ACTION_POWEROFF = "poweroff"
ACTION_REBOOT = "reboot"
ACTION_SUSPEND = "suspend"
ACTION_HIBERNATE = "hibernate"

EVENT_SESSION_STARTING = "session-starting"
EVENT_SESSION_FINISHED = "session-finished"

FRAME_HEADER = struct.Struct("!I")
SIGNED_INT = struct.Struct("!i")

TYPE_REQUEST = 1
TYPE_RESPONSE = 2
TYPE_EVENT = 3


class ProtocolError(ValueError):
    def __init__(self, message: str, raw: bytes = b"") -> None:
        super().__init__(message)
        self.raw = raw


def new_request(action: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "v": PROTOCOL_VERSION,
        "id": f"req-{uuid.uuid4()}",
        "type": "request",
        "action": action,
        "payload": payload,
    }


def new_response(request: Dict[str, Any],
                 ok: bool,
                 payload: Dict[str, Any] | None = None,
                 error: Dict[str, str] | None = None) -> Dict[str, Any]:
    response: Dict[str, Any] = {
        "v": PROTOCOL_VERSION,
        "id": request.get("id", ""),
        "type": "response",
        "action": request.get("action", ""),
        "ok": ok,
    }

    if payload is not None:
        response["payload"] = payload

    if error is not None:
        response["error"] = error

    return response


def new_error(request: Dict[str, Any], code: str, message: str) -> Dict[str, Any]:
    return new_response(request, ok=False, error={"code": code, "message": message})


def new_event(name: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "v": PROTOCOL_VERSION,
        "type": "event",
        "event": name,
        "payload": payload,
    }


def _encode_bool(value: bool) -> bytes:
    return bytes([1 if value else 0])


def _decode_bool(payload: memoryview, offset: int) -> tuple[bool, int]:
    if offset >= len(payload):
        raise ProtocolError("truncated boolean field", payload.tobytes())
    return payload[offset] != 0, offset + 1


def _encode_text(value: str) -> bytes:
    data = value.encode("utf-8")
    return FRAME_HEADER.pack(len(data)) + data


def _decode_text(payload: memoryview, offset: int) -> tuple[str, int]:
    data, offset = _decode_blob(payload, offset)
    try:
        return data.decode("utf-8"), offset
    except UnicodeDecodeError as exc:
        raise ProtocolError("invalid utf-8 in protocol field", payload.tobytes()) from exc


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
    data = payload[offset:offset + size].tobytes()
    return data, offset + size


def _decode_secbytes(payload: memoryview, offset: int) -> tuple[SecretBytes, int]:
    data, offset = _decode_blob(payload, offset)
    return SecretBytes(data), offset


def _encode_string_list(values: list[str]) -> bytes:
    encoded = bytearray(FRAME_HEADER.pack(len(values)))
    for value in values:
        encoded.extend(_encode_text(value))
    return bytes(encoded)


def _decode_string_list(payload: memoryview, offset: int) -> tuple[list[str], int]:
    if offset + FRAME_HEADER.size > len(payload):
        raise ProtocolError("truncated string list length", payload.tobytes())
    count = FRAME_HEADER.unpack(payload[offset:offset + FRAME_HEADER.size])[0]
    offset += FRAME_HEADER.size
    values = []
    for _ in range(count):
        value, offset = _decode_text(payload, offset)
        values.append(value)
    return values, offset


def _encode_signed_int(value: int) -> bytes:
    return SIGNED_INT.pack(value)


def _decode_signed_int(payload: memoryview, offset: int) -> tuple[int, int]:
    if offset + SIGNED_INT.size > len(payload):
        raise ProtocolError("truncated signed integer field", payload.tobytes())
    value = SIGNED_INT.unpack(payload[offset:offset + SIGNED_INT.size])[0]
    return value, offset + SIGNED_INT.size


def encode_message(message: Dict[str, Any]) -> bytes:
    body = bytearray()
    body.append(PROTOCOL_VERSION)

    if message.get("type") == "request":
        body.append(TYPE_REQUEST)
        body.extend(_encode_text(str(message.get("id", ""))))
        body.extend(_encode_text(str(message.get("action", ""))))
        payload = message.get("payload", {})
        if message.get("action") == ACTION_AUTH:
            body.extend(_encode_blob(payload.get("username", b"")))
            body.extend(_encode_blob(payload.get("password", b"")))
            body.extend(_encode_text(str(payload.get("command", ""))))
            body.extend(_encode_string_list(list(payload.get("desktop_names", []))))
    elif message.get("type") == "response":
        body.append(TYPE_RESPONSE)
        body.extend(_encode_text(str(message.get("id", ""))))
        body.extend(_encode_text(str(message.get("action", ""))))
        body.extend(_encode_bool(bool(message.get("ok", False))))
        if message.get("ok"):
            payload = message.get("payload", {})
            if message.get("action") == ACTION_AUTH:
                body.extend(_encode_bool(bool(payload.get("verified", False))))
            elif message.get("action") in {ACTION_POWEROFF, ACTION_REBOOT, ACTION_SUSPEND, ACTION_HIBERNATE}:
                body.extend(_encode_bool(bool(payload.get("accepted", False))))
        else:
            error = message.get("error", {})
            body.extend(_encode_text(str(error.get("code", ""))))
            body.extend(_encode_text(str(error.get("message", ""))))
    elif message.get("type") == "event":
        body.append(TYPE_EVENT)
        body.extend(_encode_text(str(message.get("event", ""))))
        payload = message.get("payload", {})
        if message.get("event") == EVENT_SESSION_STARTING:
            body.extend(_encode_text(str(payload.get("command", ""))))
            body.extend(_encode_string_list(list(payload.get("desktop_names", []))))
        elif message.get("event") == EVENT_SESSION_FINISHED:
            body.extend(_encode_signed_int(int(payload.get("pid", 0))))
            body.extend(_encode_signed_int(int(payload.get("returncode", 0))))
            body.extend(_encode_bool(bool(payload.get("failed", False))))
            body.extend(_encode_text(str(payload.get("message", ""))))
    else:
        raise ProtocolError("unknown protocol message type")

    return FRAME_HEADER.pack(len(body)) + bytes(body)


def decode_message(raw: bytes | str) -> Dict[str, Any]:
    if isinstance(raw, str):
        raw = raw.encode("utf-8")

    if len(raw) < FRAME_HEADER.size:
        raise ProtocolError("truncated protocol frame", raw)

    body_len = FRAME_HEADER.unpack(raw[:FRAME_HEADER.size])[0]
    body = raw[FRAME_HEADER.size:]
    if len(body) != body_len:
        raise ProtocolError("protocol frame length mismatch", raw)
    if len(body) < 2:
        raise ProtocolError("truncated protocol body", raw)

    payload = memoryview(body)
    if payload[0] != PROTOCOL_VERSION:
        raise ProtocolError("unsupported protocol version", raw)

    offset = 2
    msg_type = payload[1]

    if msg_type == TYPE_REQUEST:
        req_id, offset = _decode_text(payload, offset)
        action, offset = _decode_text(payload, offset)
        decoded: Dict[str, Any] = {
            "v": PROTOCOL_VERSION,
            "id": req_id,
            "type": "request",
            "action": action,
            "payload": {},
        }
        if action == ACTION_AUTH:
            username, offset = _decode_secbytes(payload, offset)
            password, offset = _decode_secbytes(payload, offset)
            command, offset = _decode_text(payload, offset)
            desktop_names, offset = _decode_string_list(payload, offset)
            decoded["payload"] = {
                "username": username,
                "password": password,
                "command": command,
                "desktop_names": desktop_names,
            }
        return decoded

    if msg_type == TYPE_RESPONSE:
        resp_id, offset = _decode_text(payload, offset)
        action, offset = _decode_text(payload, offset)
        ok, offset = _decode_bool(payload, offset)
        decoded = {
            "v": PROTOCOL_VERSION,
            "id": resp_id,
            "type": "response",
            "action": action,
            "ok": ok,
        }
        if ok:
            if action == ACTION_AUTH:
                verified, offset = _decode_bool(payload, offset)
                decoded["payload"] = {"verified": verified}
            elif action in {ACTION_POWEROFF, ACTION_REBOOT, ACTION_SUSPEND, ACTION_HIBERNATE}:
                accepted, offset = _decode_bool(payload, offset)
                decoded["payload"] = {"accepted": accepted}
        else:
            code, offset = _decode_text(payload, offset)
            message, offset = _decode_text(payload, offset)
            decoded["error"] = {"code": code, "message": message}
        return decoded

    if msg_type == TYPE_EVENT:
        event_name, offset = _decode_text(payload, offset)
        decoded = {
            "v": PROTOCOL_VERSION,
            "type": "event",
            "event": event_name,
            "payload": {},
        }
        if event_name == EVENT_SESSION_STARTING:
            command, offset = _decode_text(payload, offset)
            desktop_names, offset = _decode_string_list(payload, offset)
            decoded["payload"] = {
                "command": command,
                "desktop_names": desktop_names,
            }
        elif event_name == EVENT_SESSION_FINISHED:
            pid, offset = _decode_signed_int(payload, offset)
            returncode, offset = _decode_signed_int(payload, offset)
            failed, offset = _decode_bool(payload, offset)
            message, offset = _decode_text(payload, offset)
            decoded["payload"] = {
                "pid": pid,
                "returncode": returncode,
                "failed": failed,
                "message": message,
            }
        return decoded

    raise ProtocolError("unknown protocol message type tag", raw)


async def read_message_async(reader: asyncio.StreamReader) -> Dict[str, Any] | None:
    try:
        header = await reader.readexactly(FRAME_HEADER.size)
    except asyncio.IncompleteReadError as exc:
        if len(exc.partial) == 0:
            return None
        raise ProtocolError("truncated protocol frame header", bytes(exc.partial)) from exc

    body_len = FRAME_HEADER.unpack(header)[0]
    try:
        body = await reader.readexactly(body_len)
    except asyncio.IncompleteReadError as exc:
        raise ProtocolError("truncated protocol frame body", header + bytes(exc.partial)) from exc

    return decode_message(header + body)


def read_message_socket(sock: socket.socket) -> Dict[str, Any] | None:
    header = _recv_exact(sock, FRAME_HEADER.size)
    if header is None:
        return None
    body_len = FRAME_HEADER.unpack(header)[0]
    body = _recv_exact(sock, body_len)
    if body is None:
        raise ProtocolError("truncated protocol frame body", header)
    return decode_message(header + body)


def _recv_exact(sock: socket.socket, size: int) -> bytes | None:
    chunks = bytearray()
    while len(chunks) < size:
        chunk = sock.recv(size - len(chunks))
        if not chunk:
            if not chunks:
                return None
            break
        chunks.extend(chunk)
    if len(chunks) != size:
        return None
    return bytes(chunks)


def is_request(message: Dict[str, Any], action: str | None = None) -> bool:
    if message.get("v") != PROTOCOL_VERSION:
        return False
    if message.get("type") != "request":
        return False
    if not isinstance(message.get("id"), str) or len(message["id"]) == 0:
        return False
    if not isinstance(message.get("action"), str) or len(message["action"]) == 0:
        return False
    if not isinstance(message.get("payload"), dict):
        return False
    if action is not None and message["action"] != action:
        return False
    return True


def is_response(message: Dict[str, Any], request: Dict[str, Any] | None = None) -> bool:
    if message.get("v") != PROTOCOL_VERSION:
        return False
    if message.get("type") != "response":
        return False
    if not isinstance(message.get("id"), str):
        return False
    if not isinstance(message.get("action"), str):
        return False
    if not isinstance(message.get("ok"), bool):
        return False
    if request is not None:
        if message["id"] != request.get("id"):
            return False
        if message["action"] != request.get("action"):
            return False
    return True


def is_event(message: Dict[str, Any], name: str | None = None) -> bool:
    if message.get("v") != PROTOCOL_VERSION:
        return False
    if message.get("type") != "event":
        return False
    if not isinstance(message.get("event"), str) or len(message["event"]) == 0:
        return False
    if not isinstance(message.get("payload"), dict):
        return False
    if name is not None and message["event"] != name:
        return False
    return True
