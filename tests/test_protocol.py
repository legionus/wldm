# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026  Alexey Gladkov <legion@kernel.org>

import asyncio
import socket

import wldm.protocol
import wldm.secret


def test_new_request_creates_versioned_envelope():
    msg = wldm.protocol.new_request(wldm.protocol.ACTION_AUTH, {"username": "alice"})

    assert msg["v"] == wldm.protocol.PROTOCOL_VERSION
    assert msg["type"] == "request"
    assert msg["action"] == wldm.protocol.ACTION_AUTH
    assert msg["payload"] == {"username": "alice"}
    assert msg["id"].startswith("req-")


def test_new_response_and_error_preserve_request_identity():
    req = {"v": 1, "id": "req-1", "type": "request", "action": wldm.protocol.ACTION_AUTH, "payload": {}}

    ok = wldm.protocol.new_response(req, ok=True, payload={"verified": True})
    err = wldm.protocol.new_error(req, "bad_request", "Malformed request")

    assert ok["id"] == "req-1"
    assert ok["ok"] is True
    assert ok["payload"] == {"verified": True}
    assert err["id"] == "req-1"
    assert err["ok"] is False
    assert err["error"]["code"] == "bad_request"


def test_encode_and_decode_round_trip():
    msg = wldm.protocol.new_request(
        wldm.protocol.ACTION_AUTH,
        {"username": "alice", "password": "secret", "command": "sway", "desktop_names": ["sway"]},
    )

    decoded = wldm.protocol.decode_message(wldm.protocol.encode_message(msg))

    assert decoded["v"] == msg["v"]
    assert decoded["id"] == msg["id"]
    assert decoded["type"] == msg["type"]
    assert decoded["action"] == msg["action"]
    assert decoded["payload"]["username"].as_bytes() == b"alice"
    assert decoded["payload"]["password"].as_bytes() == b"secret"
    assert decoded["payload"]["command"] == "sway"
    assert decoded["payload"]["desktop_names"] == ["sway"]


def test_auth_field_is_too_long_checks_wire_length():
    assert wldm.protocol.auth_field_is_too_long("a" * 256) is False
    assert wldm.protocol.auth_field_is_too_long("a" * 257) is True
    assert wldm.protocol.auth_field_is_too_long(wldm.secret.SecretBytes(b"a" * 256)) is False
    assert wldm.protocol.auth_field_is_too_long(wldm.secret.SecretBytes(b"a" * 257)) is True

def test_decode_message_rejects_truncated_frame():
    try:
        wldm.protocol.decode_message(b"\x00\x00")
    except wldm.protocol.ProtocolError as exc:
        assert "truncated protocol frame" in str(exc)
    else:
        raise AssertionError("decode_message() should reject truncated frames")


def test_decode_message_rejects_oversized_frame_body():
    raw = wldm.protocol.FRAME_HEADER.pack(wldm.protocol.MAX_FRAME_BODY_LENGTH + 1)

    try:
        wldm.protocol.decode_message(raw)
    except wldm.protocol.ProtocolError as exc:
        assert "too large" in str(exc)
    else:
        raise AssertionError("decode_message() should reject oversized frames")


def test_read_message_async_rejects_oversized_frame_body():
    class DummyReader:
        def __init__(self, header: bytes):
            self.header = header
            self.calls = 0

        async def readexactly(self, size: int) -> bytes:
            self.calls += 1
            if self.calls == 1:
                return self.header
            raise AssertionError("body read should not happen for oversized frame")

    header = wldm.protocol.FRAME_HEADER.pack(wldm.protocol.MAX_FRAME_BODY_LENGTH + 1)
    reader = DummyReader(header)

    try:
        asyncio.run(wldm.protocol.read_message_async(reader))
    except wldm.protocol.ProtocolError as exc:
        assert "too large" in str(exc)
    else:
        raise AssertionError("read_message_async() should reject oversized frames")


def test_read_message_socket_rejects_oversized_frame_body():
    sock1, sock2 = socket.socketpair()
    try:
        sock1.sendall(wldm.protocol.FRAME_HEADER.pack(wldm.protocol.MAX_FRAME_BODY_LENGTH + 1))

        try:
            wldm.protocol.read_message_socket(sock2)
        except wldm.protocol.ProtocolError as exc:
            assert "too large" in str(exc)
        else:
            raise AssertionError("read_message_socket() should reject oversized frames")
    finally:
        sock1.close()
        sock2.close()


def test_is_request_validates_shape():
    msg = wldm.protocol.new_request(wldm.protocol.ACTION_AUTH, {"username": "alice"})

    assert wldm.protocol.is_request(msg) is True
    assert wldm.protocol.is_request(msg, action=wldm.protocol.ACTION_AUTH) is True
    assert wldm.protocol.is_request({"type": "request"}) is False
    assert wldm.protocol.is_request({"v": 1, "type": "request", "id": "", "action": wldm.protocol.ACTION_AUTH, "payload": {}}) is False
    assert wldm.protocol.is_request({"v": 1, "type": "request", "id": "req-1", "action": "", "payload": {}}) is False
    assert wldm.protocol.is_request({"v": 1, "type": "request", "id": "req-1", "action": wldm.protocol.ACTION_AUTH, "payload": []}) is False
    assert wldm.protocol.is_request(msg, action=wldm.protocol.ACTION_REBOOT) is False


def test_new_event_and_validators():
    event = wldm.protocol.new_event(wldm.protocol.EVENT_SESSION_STARTING, {"username": "alice"})
    req = wldm.protocol.new_request(wldm.protocol.ACTION_AUTH, {"username": "alice"})
    resp = wldm.protocol.new_response(req, ok=True, payload={"verified": True})

    assert wldm.protocol.is_event(event) is True
    assert wldm.protocol.is_event(event, name=wldm.protocol.EVENT_SESSION_STARTING) is True
    assert wldm.protocol.is_response(resp, req) is True
    assert wldm.protocol.is_response(event) is False
    assert wldm.protocol.is_response({"v": 0, "type": "response", "id": "req-1", "action": wldm.protocol.ACTION_AUTH, "ok": True}) is False
    assert wldm.protocol.is_response({"v": 1, "type": "response", "id": 1, "action": wldm.protocol.ACTION_AUTH, "ok": True}) is False
    assert wldm.protocol.is_response({"v": 1, "type": "response", "id": "req-1", "action": 1, "ok": True}) is False
    assert wldm.protocol.is_response({"v": 1, "type": "response", "id": "req-1", "action": wldm.protocol.ACTION_AUTH, "ok": "yes"}) is False
    assert wldm.protocol.is_response(resp, {"id": "other", "action": wldm.protocol.ACTION_AUTH}) is False
    assert wldm.protocol.is_response(resp, {"id": req["id"], "action": wldm.protocol.ACTION_REBOOT}) is False
    assert wldm.protocol.is_event({"v": 0, "type": "event", "event": "x", "payload": {}}) is False
    assert wldm.protocol.is_event({"v": 1, "type": "event", "event": "", "payload": {}}) is False
    assert wldm.protocol.is_event({"v": 1, "type": "event", "event": "x", "payload": []}) is False
    assert wldm.protocol.is_event(event, name=wldm.protocol.EVENT_SESSION_FINISHED) is False


def test_new_request_supports_control_actions():
    poweroff = wldm.protocol.new_request(wldm.protocol.ACTION_POWEROFF, {})
    reboot = wldm.protocol.new_request(wldm.protocol.ACTION_REBOOT, {})
    suspend = wldm.protocol.new_request(wldm.protocol.ACTION_SUSPEND, {})
    hibernate = wldm.protocol.new_request(wldm.protocol.ACTION_HIBERNATE, {})

    assert poweroff["action"] == wldm.protocol.ACTION_POWEROFF
    assert reboot["action"] == wldm.protocol.ACTION_REBOOT
    assert suspend["action"] == wldm.protocol.ACTION_SUSPEND
    assert hibernate["action"] == wldm.protocol.ACTION_HIBERNATE


def test_encode_and_decode_get_state_response():
    req = wldm.protocol.new_request(wldm.protocol.ACTION_GET_STATE, {})
    msg = wldm.protocol.new_response(
        req,
        ok=True,
        payload={
            "seat": "seat0",
            "greeter_ready": True,
            "active_sessions": [{"pid": 42, "username": "alice", "command": "sway"}],
        },
    )

    decoded = wldm.protocol.decode_message(wldm.protocol.encode_message(msg))

    assert decoded["payload"] == {
        "seat": "seat0",
        "greeter_ready": True,
        "active_sessions": [{"pid": 42, "username": "alice", "command": "sway"}],
    }


def test_encode_and_decode_state_changed_event():
    msg = wldm.protocol.new_event(
        wldm.protocol.EVENT_STATE_CHANGED,
        {
            "seat": "seat0",
            "greeter_ready": False,
            "active_sessions": [],
        },
    )

    decoded = wldm.protocol.decode_message(wldm.protocol.encode_message(msg))

    assert decoded == msg
