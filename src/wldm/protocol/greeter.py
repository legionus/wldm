#!/usr/bin/env python
# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026  Alexey Gladkov <legion@kernel.org>

import asyncio
import socket
import uuid

from typing import Any, Dict

from wldm.protocol import framing
from wldm.secret import SecretBytes


PROTOCOL_VERSION = 1

ACTION_CREATE_SESSION = "create-session"
ACTION_CONTINUE_SESSION = "continue-session"
ACTION_CANCEL_SESSION = "cancel-session"
ACTION_START_SESSION = "start-session"
ACTION_GET_STATE = "get-state"
ACTION_POWEROFF = "poweroff"
ACTION_REBOOT = "reboot"
ACTION_SUSPEND = "suspend"
ACTION_HIBERNATE = "hibernate"
CONTROL_ACTIONS = {
    ACTION_POWEROFF,
    ACTION_REBOOT,
    ACTION_SUSPEND,
    ACTION_HIBERNATE,
}

EVENT_SESSION_STARTING = "session-starting"
EVENT_SESSION_FINISHED = "session-finished"
EVENT_STATE_CHANGED = "state-changed"
EVENT_REEXEC = "re-exec"

TYPE_REQUEST = 1
TYPE_RESPONSE = 2
TYPE_EVENT = 3

AUTH_FIELD_MAX_LENGTH = 256


class ProtocolError(ValueError):
    def __init__(self, message: str, raw: bytes = b"") -> None:
        super().__init__(message)
        self.raw = raw


def auth_field_length(value: Any) -> int:
    """Return the wire-visible length of one auth field.

    Args:
        value: Username or password value to measure.

    Returns:
        Length of the encoded protocol field in bytes.
    """
    if isinstance(value, SecretBytes):
        return len(value)

    if isinstance(value, str):
        return len(value.encode("utf-8"))

    if isinstance(value, (bytes, bytearray, memoryview)):
        return len(value)

    return len(str(value).encode("utf-8"))


def auth_field_is_too_long(value: Any) -> bool:
    """Check whether one auth field exceeds the protocol limit.

    Args:
        value: Username or password value to validate.

    Returns:
        `True` if the field is longer than `AUTH_FIELD_MAX_LENGTH`.
    """
    return auth_field_length(value) > AUTH_FIELD_MAX_LENGTH


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


def new_conversation_response(request: Dict[str, Any],
                              state: str,
                              style: str = "",
                              text: str = "") -> Dict[str, Any]:
    payload: Dict[str, Any] = {"state": state}

    if style or text:
        payload["message"] = {"style": style, "text": text}

    return new_response(request, ok=True, payload=payload)


def _decode_bool(payload: memoryview, offset: int) -> tuple[bool, int]:
    return framing.decode_bool(payload, offset, ProtocolError)


def _decode_text(payload: memoryview, offset: int) -> tuple[str, int]:
    return framing.decode_text(payload, offset, ProtocolError)


def _decode_blob(payload: memoryview, offset: int) -> tuple[bytes, int]:
    return framing.decode_blob(payload, offset, ProtocolError)


def _decode_secbytes(payload: memoryview, offset: int) -> tuple[SecretBytes, int]:
    return framing.decode_secbytes(payload, offset, ProtocolError)


def _encode_string_list(values: list[str]) -> bytes:
    encoded = bytearray(framing.FRAME_HEADER.pack(len(values)))

    for value in values:
        encoded.extend(framing.encode_text(value))

    return bytes(encoded)


def _decode_string_list(payload: memoryview, offset: int) -> tuple[list[str], int]:
    if offset + framing.FRAME_HEADER.size > len(payload):
        raise ProtocolError("truncated string list length", payload.tobytes())

    count = framing.FRAME_HEADER.unpack(payload[offset:offset + framing.FRAME_HEADER.size])[0]
    offset += framing.FRAME_HEADER.size

    values = []

    for _ in range(count):
        value, offset = _decode_text(payload, offset)
        values.append(value)

    return values, offset


def _decode_signed_int(payload: memoryview, offset: int) -> tuple[int, int]:
    return framing.decode_signed_int(payload, offset, ProtocolError)


def _encode_response_payload(body: bytearray, action: str, payload: Dict[str, Any]) -> None:
    if action in {ACTION_CREATE_SESSION, ACTION_CONTINUE_SESSION}:
        body.extend(framing.encode_text(str(payload.get("state", ""))))
        message = payload.get("message")
        if isinstance(message, dict):
            body.extend(framing.encode_bool(True))
            message_payload = message
            body.extend(framing.encode_text(str(message_payload.get("style", ""))))
            body.extend(framing.encode_text(str(message_payload.get("text", ""))))
        else:
            body.extend(framing.encode_bool(False))

    elif action == ACTION_GET_STATE:
        body.extend(framing.encode_text(str(payload.get("seat", ""))))
        body.extend(framing.encode_bool(bool(payload.get("greeter_ready", False))))

        sessions = list(payload.get("active_sessions", []))
        body.extend(framing.FRAME_HEADER.pack(len(sessions)))

        for session in sessions:
            body.extend(framing.encode_signed_int(int(session.get("pid", 0))))
            body.extend(framing.encode_text(str(session.get("username", ""))))
            body.extend(framing.encode_text(str(session.get("command", ""))))

    elif action in CONTROL_ACTIONS:
        body.extend(framing.encode_bool(bool(payload.get("accepted", False))))


def _decode_response_payload(action: str, payload: memoryview, offset: int) -> tuple[Dict[str, Any], int]:
    if action in {ACTION_CREATE_SESSION, ACTION_CONTINUE_SESSION}:
        state, offset = _decode_text(payload, offset)
        has_message, offset = _decode_bool(payload, offset)
        decoded: Dict[str, Any] = {"state": state}

        if has_message:
            style, offset = _decode_text(payload, offset)
            text, offset = _decode_text(payload, offset)
            decoded["message"] = {"style": style, "text": text}

        return decoded, offset

    if action == ACTION_GET_STATE:
        seat, offset = _decode_text(payload, offset)
        greeter_ready, offset = _decode_bool(payload, offset)

        if offset + framing.FRAME_HEADER.size > len(payload):
            raise ProtocolError("truncated active session list length", payload.tobytes())

        count = framing.FRAME_HEADER.unpack(payload[offset:offset + framing.FRAME_HEADER.size])[0]
        offset += framing.FRAME_HEADER.size

        sessions = []

        for _ in range(count):
            pid, offset = _decode_signed_int(payload, offset)
            username, offset = _decode_text(payload, offset)
            command, offset = _decode_text(payload, offset)
            sessions.append({"pid": pid, "username": username, "command": command})

        return {
            "seat": seat,
            "greeter_ready": greeter_ready,
            "active_sessions": sessions,
        }, offset

    if action in CONTROL_ACTIONS:
        accepted, offset = _decode_bool(payload, offset)
        return {"accepted": accepted}, offset

    return {}, offset


def _finish_decoded_message(decoded: Dict[str, Any],
                            payload: memoryview,
                            offset: int,
                            raw: bytes) -> Dict[str, Any]:
    if offset != len(payload):
        raise ProtocolError("trailing bytes after protocol message", raw)

    return decoded


def _is_versioned_type(message: Dict[str, Any], message_type: str) -> bool:
    return message.get("v") == PROTOCOL_VERSION and message.get("type") == message_type


def _has_fields(message: Dict[str, Any], fields: tuple[tuple[str, type, bool], ...]) -> bool:
    for key, value_type, nonempty in fields:
        value = message.get(key)

        if not isinstance(value, value_type):
            return False

        if nonempty and isinstance(value, str) and len(value) == 0:
            return False

    return True


def encode_message(message: Dict[str, Any]) -> bytes:
    body = bytearray()
    body.append(PROTOCOL_VERSION)

    message_type = message.get("type")

    if message_type == "request":
        body.append(TYPE_REQUEST)

        body.extend(framing.encode_text(str(message.get("id", ""))))
        body.extend(framing.encode_text(str(message.get("action", ""))))

        payload = message.get("payload", {})

        if message.get("action") == ACTION_CREATE_SESSION:
            body.extend(framing.encode_blob(payload.get("username", b"")))
        elif message.get("action") == ACTION_CONTINUE_SESSION:
            body.extend(framing.encode_blob(payload.get("response", b"")))
        elif message.get("action") == ACTION_START_SESSION:
            body.extend(framing.encode_text(str(payload.get("command", ""))))
            body.extend(_encode_string_list(list(payload.get("desktop_names", []))))
            body.extend(framing.encode_text(str(payload.get("name", ""))))
            body.extend(framing.encode_text(str(payload.get("icon", ""))))
            body.extend(framing.encode_text(str(payload.get("desktop_file", ""))))

    elif message_type == "response":
        body.append(TYPE_RESPONSE)

        body.extend(framing.encode_text(str(message.get("id", ""))))
        body.extend(framing.encode_text(str(message.get("action", ""))))

        ok = bool(message.get("ok", False))
        body.extend(framing.encode_bool(ok))

        if ok:
            _encode_response_payload(body, str(message.get("action", "")), message.get("payload", {}))
        else:
            error = message.get("error", {})
            body.extend(framing.encode_text(str(error.get("code", ""))))
            body.extend(framing.encode_text(str(error.get("message", ""))))

    elif message_type == "event":
        body.append(TYPE_EVENT)

        body.extend(framing.encode_text(str(message.get("event", ""))))

        payload = message.get("payload", {})

        if message.get("event") == EVENT_SESSION_STARTING:
            body.extend(framing.encode_text(str(payload.get("command", ""))))
            body.extend(_encode_string_list(list(payload.get("desktop_names", []))))

        elif message.get("event") == EVENT_SESSION_FINISHED:
            body.extend(framing.encode_signed_int(int(payload.get("pid", 0))))
            body.extend(framing.encode_signed_int(int(payload.get("returncode", 0))))
            body.extend(framing.encode_bool(bool(payload.get("failed", False))))
            body.extend(framing.encode_text(str(payload.get("message", ""))))

        elif message.get("event") == EVENT_STATE_CHANGED:
            _encode_response_payload(body, ACTION_GET_STATE, payload)
    else:
        raise ProtocolError("unknown protocol message type")

    return framing.encode_frame(body, framing.MAX_FRAME_BODY_LENGTH, ProtocolError)


def decode_message(raw: bytes | str) -> Dict[str, Any]:
    if isinstance(raw, str):
        raw = raw.encode("utf-8")

    payload = framing.frame_payload(raw, framing.MAX_FRAME_BODY_LENGTH, ProtocolError)

    if len(payload) < 2:
        raise ProtocolError("truncated protocol body", raw)

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

        if action == ACTION_CREATE_SESSION:
            username, offset = _decode_secbytes(payload, offset)
            decoded["payload"] = {"username": username}
        elif action == ACTION_CONTINUE_SESSION:
            response, offset = _decode_secbytes(payload, offset)
            decoded["payload"] = {"response": response}
        elif action == ACTION_START_SESSION:
            command, offset = _decode_text(payload, offset)
            desktop_names, offset = _decode_string_list(payload, offset)
            name, offset = _decode_text(payload, offset)
            icon, offset = _decode_text(payload, offset)
            desktop_file, offset = _decode_text(payload, offset)
            decoded["payload"] = {
                "command": command,
                "desktop_names": desktop_names,
                "name": name,
                "icon": icon,
                "desktop_file": desktop_file,
            }

        return _finish_decoded_message(decoded, payload, offset, raw)

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
            decoded["payload"], offset = _decode_response_payload(action, payload, offset)
        else:
            code, offset = _decode_text(payload, offset)
            message, offset = _decode_text(payload, offset)

            decoded["error"] = {"code": code, "message": message}

        return _finish_decoded_message(decoded, payload, offset, raw)

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

        elif event_name == EVENT_STATE_CHANGED:
            decoded["payload"], offset = _decode_response_payload(ACTION_GET_STATE, payload, offset)

        return _finish_decoded_message(decoded, payload, offset, raw)

    raise ProtocolError("unknown protocol message type tag", raw)


async def read_message_async(reader: asyncio.StreamReader) -> Dict[str, Any] | None:
    frame = await framing.read_frame_async(
        reader,
        framing.MAX_FRAME_BODY_LENGTH,
        ProtocolError,
        "truncated protocol frame header",
    )
    if frame is None:
        return None

    return decode_message(frame)


def read_message_socket(sock: socket.socket) -> Dict[str, Any] | None:
    frame = framing.read_frame_socket(sock, framing.MAX_FRAME_BODY_LENGTH, ProtocolError)
    if frame is None:
        return None

    return decode_message(frame)


def is_request(message: Dict[str, Any], action: str | None = None) -> bool:
    if not _is_versioned_type(message, "request"):
        return False

    if not _has_fields(message, (("id", str, True), ("action", str, True), ("payload", dict, False))):
        return False

    if action is not None and message["action"] != action:
        return False

    return True


def is_response(message: Dict[str, Any], request: Dict[str, Any] | None = None) -> bool:
    if not _is_versioned_type(message, "response"):
        return False

    if not _has_fields(message, (("id", str, False), ("action", str, False), ("ok", bool, False))):
        return False

    if request is not None:
        if message["id"] != request.get("id"):
            return False

        if message["action"] != request.get("action"):
            return False

    return True


def is_event(message: Dict[str, Any], name: str | None = None) -> bool:
    if not _is_versioned_type(message, "event"):
        return False

    if not _has_fields(message, (("event", str, True), ("payload", dict, False))):
        return False

    if name is not None and message["event"] != name:
        return False

    return True
