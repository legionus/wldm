# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026  Alexey Gladkov <legion@kernel.org>

import asyncio
import socket

import wldm.greeter_protocol as greeter_protocol
import wldm.secret


class ChunkReader:
    def __init__(self, chunks, on_exhaustion="unexpected read"):
        self.chunks = iter(chunks)
        self.on_exhaustion = on_exhaustion

    async def readexactly(self, size):
        try:
            chunk = next(self.chunks)
        except StopIteration as exc:
            raise AssertionError(self.on_exhaustion) from exc
        assert len(chunk) == size
        return chunk


def test_new_request_creates_versioned_envelope():
    msg = greeter_protocol.new_request(greeter_protocol.ACTION_CREATE_SESSION, {"username": "alice"})

    assert msg["v"] == greeter_protocol.PROTOCOL_VERSION
    assert msg["type"] == "request"
    assert msg["action"] == greeter_protocol.ACTION_CREATE_SESSION
    assert msg["payload"] == {"username": "alice"}
    assert msg["id"].startswith("req-")


def test_new_response_and_error_preserve_request_identity():
    req = {"v": 1, "id": "req-1", "type": "request", "action": greeter_protocol.ACTION_CREATE_SESSION, "payload": {}}

    ok = greeter_protocol.new_conversation_response(req, "ready")
    err = greeter_protocol.new_error(req, "bad_request", "Malformed request")

    assert ok["id"] == "req-1"
    assert ok["ok"] is True
    assert ok["payload"] == {"state": "ready"}
    assert err["id"] == "req-1"
    assert err["ok"] is False
    assert err["error"]["code"] == "bad_request"


def test_encode_and_decode_round_trip():
    msg = greeter_protocol.new_request(
        greeter_protocol.ACTION_START_SESSION,
        {"command": "sway", "desktop_names": ["sway"]},
    )

    decoded = greeter_protocol.decode_message(greeter_protocol.encode_message(msg))

    assert decoded["v"] == msg["v"]
    assert decoded["id"] == msg["id"]
    assert decoded["type"] == msg["type"]
    assert decoded["action"] == msg["action"]
    assert decoded["payload"]["command"] == "sway"
    assert decoded["payload"]["desktop_names"] == ["sway"]


def test_encode_and_decode_create_session_request():
    msg = greeter_protocol.new_request(
        greeter_protocol.ACTION_CREATE_SESSION,
        {"username": "alice"},
    )

    decoded = greeter_protocol.decode_message(greeter_protocol.encode_message(msg))

    assert decoded["action"] == greeter_protocol.ACTION_CREATE_SESSION
    assert decoded["payload"]["username"].as_bytes() == b"alice"


def test_encode_and_decode_continue_session_request():
    msg = greeter_protocol.new_request(
        greeter_protocol.ACTION_CONTINUE_SESSION,
        {"response": "secret"},
    )

    decoded = greeter_protocol.decode_message(greeter_protocol.encode_message(msg))

    assert decoded["action"] == greeter_protocol.ACTION_CONTINUE_SESSION
    assert decoded["payload"]["response"].as_bytes() == b"secret"


def test_encode_and_decode_start_session_request():
    msg = greeter_protocol.new_request(
        greeter_protocol.ACTION_START_SESSION,
        {"command": "sway", "desktop_names": ["sway", "wlroots"]},
    )

    decoded = greeter_protocol.decode_message(greeter_protocol.encode_message(msg))

    assert decoded["action"] == greeter_protocol.ACTION_START_SESSION
    assert decoded["payload"] == {"command": "sway", "desktop_names": ["sway", "wlroots"]}


def test_encode_and_decode_conversation_response():
    req = greeter_protocol.new_request(greeter_protocol.ACTION_CREATE_SESSION, {"username": "alice"})
    msg = greeter_protocol.new_conversation_response(req, "pending", style="secret", text="Password:")

    decoded = greeter_protocol.decode_message(greeter_protocol.encode_message(msg))

    assert decoded["payload"] == {
        "state": "pending",
        "message": {"style": "secret", "text": "Password:"},
    }


def test_auth_field_is_too_long_checks_wire_length():
    assert greeter_protocol.auth_field_is_too_long("a" * 256) is False
    assert greeter_protocol.auth_field_is_too_long("a" * 257) is True
    assert greeter_protocol.auth_field_is_too_long(wldm.secret.SecretBytes(b"a" * 256)) is False
    assert greeter_protocol.auth_field_is_too_long(wldm.secret.SecretBytes(b"a" * 257)) is True

def test_decode_message_rejects_truncated_frame():
    try:
        greeter_protocol.decode_message(b"\x00\x00")
    except greeter_protocol.ProtocolError as exc:
        assert "truncated protocol frame" in str(exc)
    else:
        raise AssertionError("decode_message() should reject truncated frames")


def test_decode_message_rejects_oversized_frame_body():
    raw = greeter_protocol.FRAME_HEADER.pack(greeter_protocol.MAX_FRAME_BODY_LENGTH + 1)

    try:
        greeter_protocol.decode_message(raw)
    except greeter_protocol.ProtocolError as exc:
        assert "too large" in str(exc)
    else:
        raise AssertionError("decode_message() should reject oversized frames")


def test_read_message_async_rejects_oversized_frame_body():
    header = greeter_protocol.FRAME_HEADER.pack(greeter_protocol.MAX_FRAME_BODY_LENGTH + 1)
    reader = ChunkReader([header], on_exhaustion="body read should not happen for oversized frame")

    try:
        asyncio.run(greeter_protocol.read_message_async(reader))
    except greeter_protocol.ProtocolError as exc:
        assert "too large" in str(exc)
    else:
        raise AssertionError("read_message_async() should reject oversized frames")


def test_read_message_socket_rejects_oversized_frame_body():
    sock1, sock2 = socket.socketpair()
    try:
        sock1.sendall(greeter_protocol.FRAME_HEADER.pack(greeter_protocol.MAX_FRAME_BODY_LENGTH + 1))

        try:
            greeter_protocol.read_message_socket(sock2)
        except greeter_protocol.ProtocolError as exc:
            assert "too large" in str(exc)
        else:
            raise AssertionError("read_message_socket() should reject oversized frames")
    finally:
        sock1.close()
        sock2.close()


def test_is_request_validates_shape():
    msg = greeter_protocol.new_request(greeter_protocol.ACTION_CREATE_SESSION, {"username": "alice"})

    assert greeter_protocol.is_request(msg) is True
    assert greeter_protocol.is_request(msg, action=greeter_protocol.ACTION_CREATE_SESSION) is True
    assert greeter_protocol.is_request({"type": "request"}) is False
    assert greeter_protocol.is_request(
        {"v": 1, "type": "request", "id": "", "action": greeter_protocol.ACTION_CREATE_SESSION, "payload": {}}
    ) is False
    assert greeter_protocol.is_request({"v": 1, "type": "request", "id": "req-1", "action": "", "payload": {}}) is False
    assert greeter_protocol.is_request(
        {"v": 1, "type": "request", "id": "req-1", "action": greeter_protocol.ACTION_CREATE_SESSION, "payload": []}
    ) is False
    assert greeter_protocol.is_request(msg, action=greeter_protocol.ACTION_REBOOT) is False


def test_new_event_and_validators():
    event = greeter_protocol.new_event(greeter_protocol.EVENT_SESSION_STARTING, {"username": "alice"})
    req = greeter_protocol.new_request(greeter_protocol.ACTION_CREATE_SESSION, {"username": "alice"})
    resp = greeter_protocol.new_conversation_response(req, "ready")

    assert greeter_protocol.is_event(event) is True
    assert greeter_protocol.is_event(event, name=greeter_protocol.EVENT_SESSION_STARTING) is True
    assert greeter_protocol.is_response(resp, req) is True
    assert greeter_protocol.is_response(event) is False
    assert greeter_protocol.is_response(
        {"v": 0, "type": "response", "id": "req-1", "action": greeter_protocol.ACTION_CREATE_SESSION, "ok": True}
    ) is False
    assert greeter_protocol.is_response(
        {"v": 1, "type": "response", "id": 1, "action": greeter_protocol.ACTION_CREATE_SESSION, "ok": True}
    ) is False
    assert greeter_protocol.is_response({"v": 1, "type": "response", "id": "req-1", "action": 1, "ok": True}) is False
    assert greeter_protocol.is_response(
        {"v": 1, "type": "response", "id": "req-1", "action": greeter_protocol.ACTION_CREATE_SESSION, "ok": "yes"}
    ) is False
    assert greeter_protocol.is_response(resp, {"id": "other", "action": greeter_protocol.ACTION_CREATE_SESSION}) is False
    assert greeter_protocol.is_response(resp, {"id": req["id"], "action": greeter_protocol.ACTION_REBOOT}) is False
    assert greeter_protocol.is_event({"v": 0, "type": "event", "event": "x", "payload": {}}) is False
    assert greeter_protocol.is_event({"v": 1, "type": "event", "event": "", "payload": {}}) is False
    assert greeter_protocol.is_event({"v": 1, "type": "event", "event": "x", "payload": []}) is False
    assert greeter_protocol.is_event(event, name=greeter_protocol.EVENT_SESSION_FINISHED) is False


def test_new_request_supports_control_actions():
    poweroff = greeter_protocol.new_request(greeter_protocol.ACTION_POWEROFF, {})
    reboot = greeter_protocol.new_request(greeter_protocol.ACTION_REBOOT, {})
    suspend = greeter_protocol.new_request(greeter_protocol.ACTION_SUSPEND, {})
    hibernate = greeter_protocol.new_request(greeter_protocol.ACTION_HIBERNATE, {})

    assert poweroff["action"] == greeter_protocol.ACTION_POWEROFF
    assert reboot["action"] == greeter_protocol.ACTION_REBOOT
    assert suspend["action"] == greeter_protocol.ACTION_SUSPEND
    assert hibernate["action"] == greeter_protocol.ACTION_HIBERNATE


def test_encode_and_decode_get_state_response():
    req = greeter_protocol.new_request(greeter_protocol.ACTION_GET_STATE, {})
    msg = greeter_protocol.new_response(
        req,
        ok=True,
        payload={
            "seat": "seat0",
            "greeter_ready": True,
            "active_sessions": [{"pid": 42, "username": "alice", "command": "sway"}],
        },
    )

    decoded = greeter_protocol.decode_message(greeter_protocol.encode_message(msg))

    assert decoded["payload"] == {
        "seat": "seat0",
        "greeter_ready": True,
        "active_sessions": [{"pid": 42, "username": "alice", "command": "sway"}],
    }


def test_encode_and_decode_state_changed_event():
    msg = greeter_protocol.new_event(
        greeter_protocol.EVENT_STATE_CHANGED,
        {
            "seat": "seat0",
            "greeter_ready": False,
            "active_sessions": [],
        },
    )

    decoded = greeter_protocol.decode_message(greeter_protocol.encode_message(msg))

    assert decoded == msg
