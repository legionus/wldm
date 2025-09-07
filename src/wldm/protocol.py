#!/usr/bin/env python
# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026  Alexey Gladkov <legion@kernel.org>

import json
import uuid

from typing import Any, Dict


PROTOCOL_VERSION = 1

ACTION_AUTH = "auth"
ACTION_POWEROFF = "poweroff"
ACTION_REBOOT = "reboot"

EVENT_SESSION_STARTING = "session-starting"
EVENT_SESSION_FINISHED = "session-finished"


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


def encode_message(message: Dict[str, Any]) -> str:
    return json.dumps(message)


def decode_message(raw: str) -> Dict[str, Any]:
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("protocol message must be a JSON object")
    return data


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
