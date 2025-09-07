import wldm.protocol


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
    msg = wldm.protocol.new_request(wldm.protocol.ACTION_AUTH, {"username": "alice"})

    assert wldm.protocol.decode_message(wldm.protocol.encode_message(msg)) == msg


def test_decode_message_rejects_non_object_json():
    try:
        wldm.protocol.decode_message("[]")
    except ValueError as exc:
        assert "JSON object" in str(exc)
    else:
        raise AssertionError("decode_message() should reject non-object JSON")


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

    assert poweroff["action"] == wldm.protocol.ACTION_POWEROFF
    assert reboot["action"] == wldm.protocol.ACTION_REBOOT
