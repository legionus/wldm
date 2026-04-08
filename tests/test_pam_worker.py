# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026  Alexey Gladkov <legion@kernel.org>

import ctypes
import socket
from types import SimpleNamespace

import wldm._pam_ffi as ffi
import wldm.pam_worker as pam_worker
import wldm.pam_worker_protocol as pam_worker_protocol


def pam_messages(*items):
    messages = []
    pointers = (ctypes.POINTER(ffi.PamMessage) * len(items))()

    for index, (style, text) in enumerate(items):
        message = ffi.PamMessage(style, text.encode())
        messages.append(message)
        pointers[index] = ctypes.pointer(messages[index])

    return messages, pointers


def test_prompt_broker_returns_answer_and_cancel():
    left, right = socket.socketpair()

    try:
        left.sendall(pam_worker_protocol.encode_message(pam_worker_protocol.new_answer(b"secret")))
        broker = pam_worker.PromptBroker(right, service="login", username="alice", tty="/dev/tty7")
        answer = broker.ask("secret", "Password:")
        prompt = pam_worker_protocol.read_message_socket(left)
    finally:
        left.close()
        right.close()

    assert answer == b"secret"
    assert prompt == {"v": 1, "kind": "prompt", "style": "secret", "text": "Password:"}

    left, right = socket.socketpair()
    try:
        left.sendall(pam_worker_protocol.encode_message(pam_worker_protocol.new_cancel()))
        broker = pam_worker.PromptBroker(right, service="login", username="alice", tty="/dev/tty7")
        answer = broker.ask("info", "Continue")
    finally:
        left.close()
        right.close()

    assert answer is None


def test_prompt_broker_rejects_unexpected_reply():
    left, right = socket.socketpair()

    try:
        left.sendall(pam_worker_protocol.encode_message(pam_worker_protocol.new_ready()))
        broker = pam_worker.PromptBroker(right, service="login", username="alice", tty="/dev/tty7")

        try:
            broker.ask("secret", "Password:")
        except pam_worker.ConversationError as exc:
            assert "unexpected PAM worker reply" in str(exc)
        else:
            raise AssertionError("unexpected reply should fail")
    finally:
        left.close()
        right.close()


def test_prompt_broker_rejects_closed_channel():
    left, right = socket.socketpair()

    try:
        left.close()
        broker = pam_worker.PromptBroker(right, service="login", username="alice", tty="/dev/tty7")

        try:
            broker.ask("secret", "Password:")
        except pam_worker.ConversationError as exc:
            assert "closed PAM worker channel" in str(exc)
        else:
            raise AssertionError("closed channel should fail")
    finally:
        right.close()


def test_prompt_broker_accepts_bytes_like_payloads_and_rejects_bad_payload(monkeypatch):
    sock = SimpleNamespace(sendall=lambda data: None)
    broker = pam_worker.PromptBroker(sock, service="login", username="alice", tty="/dev/tty7")

    monkeypatch.setattr(
        pam_worker.worker_protocol,
        "read_message_socket",
        lambda _sock: {"kind": pam_worker_protocol.KIND_ANSWER, "response": memoryview(b"otp")},
    )
    assert broker.ask("visible", "Code:") == b"otp"

    monkeypatch.setattr(
        pam_worker.worker_protocol,
        "read_message_socket",
        lambda _sock: {"kind": pam_worker_protocol.KIND_ANSWER, "response": object()},
    )

    try:
        broker.ask("visible", "Code:")
    except pam_worker.ConversationError as exc:
        assert "unexpected PAM worker answer payload" in str(exc)
    else:
        raise AssertionError("unsupported answer payload should fail")


def test_broker_registry_registers_unregisters_and_rejects_unknown():
    broker = pam_worker.PromptBroker(SimpleNamespace(), service="login", username="alice", tty="/dev/tty7")
    broker_id = pam_worker._register_broker(broker)

    try:
        assert pam_worker._broker(broker_id) is broker
    finally:
        pam_worker._unregister_broker(broker_id)

    try:
        pam_worker._broker(broker_id)
    except pam_worker.ConversationError as exc:
        assert "unknown PAM worker broker" in str(exc)
    else:
        raise AssertionError("unknown broker lookup should fail")


def test_prompt_style_maps_supported_pam_styles():
    assert pam_worker._prompt_style(ffi.PAM_PROMPT_ECHO_OFF) == "secret"
    assert pam_worker._prompt_style(ffi.PAM_PROMPT_ECHO_ON) == "visible"
    assert pam_worker._prompt_style(ffi.PAM_TEXT_INFO) == "info"
    assert pam_worker._prompt_style(ffi.PAM_ERROR_MSG) == "error"


def test_conversation_conv_populates_response_for_secret_prompt(monkeypatch):
    broker = SimpleNamespace(ask=lambda style, text: b"secret")
    monkeypatch.setattr(pam_worker, "_broker", lambda broker_id: broker)

    _, msgs = pam_messages((ffi.PAM_PROMPT_ECHO_OFF, "Password:"))
    response = (ctypes.POINTER(ffi.PamResponse) * 1)()

    rc = pam_worker._conversation_conv(1, msgs, response, ctypes.c_void_p(123))

    assert rc == ffi.PAM_SUCCESS
    assert ctypes.string_at(response[0][0].resp) == b"secret"


def test_conversation_conv_accepts_info_prompt_without_response(monkeypatch):
    broker = SimpleNamespace(ask=lambda style, text: b"")
    monkeypatch.setattr(pam_worker, "_broker", lambda broker_id: broker)

    _, msgs = pam_messages((ffi.PAM_TEXT_INFO, "Hello"))
    response = (ctypes.POINTER(ffi.PamResponse) * 1)()

    rc = pam_worker._conversation_conv(1, msgs, response, ctypes.c_void_p(123))

    assert rc == ffi.PAM_SUCCESS
    assert response[0][0].resp is None


def test_conversation_conv_rejects_missing_appdata_and_bad_style(monkeypatch):
    _, msgs = pam_messages((ffi.PAM_PROMPT_ECHO_OFF, "Password:"))
    response = (ctypes.POINTER(ffi.PamResponse) * 1)()

    assert pam_worker._conversation_conv(1, msgs, response, None) == ffi.PAM_CONV_ERR

    broker = SimpleNamespace(ask=lambda style, text: b"secret")
    monkeypatch.setattr(pam_worker, "_broker", lambda broker_id: broker)
    _, bad_msgs = pam_messages((99, "???"))
    assert pam_worker._conversation_conv(1, bad_msgs, response, ctypes.c_void_p(123)) == ffi.PAM_CONV_ERR


def test_conversation_conv_rejects_unknown_broker_and_allocation_failure(monkeypatch):
    _, msgs = pam_messages((ffi.PAM_PROMPT_ECHO_OFF, "Password:"))
    response = (ctypes.POINTER(ffi.PamResponse) * 1)()

    monkeypatch.setattr(pam_worker, "_broker", lambda broker_id: (_ for _ in ()).throw(pam_worker.ConversationError("no broker")))
    assert pam_worker._conversation_conv(1, msgs, response, ctypes.c_void_p(123)) == ffi.PAM_CONV_ERR

    monkeypatch.setattr(pam_worker, "_broker", lambda broker_id: SimpleNamespace(ask=lambda style, text: b"secret"))
    monkeypatch.setattr(pam_worker, "calloc", lambda count, size: None)
    assert pam_worker._conversation_conv(1, msgs, response, ctypes.c_void_p(123)) == ffi.PAM_CONV_ERR


def test_conversation_conv_rejects_cancelled_prompt(monkeypatch):
    broker = SimpleNamespace(ask=lambda style, text: None)
    monkeypatch.setattr(pam_worker, "_broker", lambda broker_id: broker)

    _, msgs = pam_messages((ffi.PAM_PROMPT_ECHO_OFF, "Password:"))
    response = (ctypes.POINTER(ffi.PamResponse) * 1)()

    assert pam_worker._conversation_conv(1, msgs, response, ctypes.c_void_p(123)) == ffi.PAM_CONV_ERR


def test_conversation_conv_rejects_response_buffer_and_callback_failures(monkeypatch):
    response = (ctypes.POINTER(ffi.PamResponse) * 1)()
    broker = SimpleNamespace(ask=lambda style, text: b"secret")
    monkeypatch.setattr(pam_worker, "_broker", lambda broker_id: broker)
    monkeypatch.setattr(pam_worker, "_copy_response_bytes", lambda data: None)

    _, msgs = pam_messages((ffi.PAM_PROMPT_ECHO_OFF, "Password:"))
    assert pam_worker._conversation_conv(1, msgs, response, ctypes.c_void_p(123)) == ffi.PAM_CONV_ERR

    failing_broker = SimpleNamespace(ask=lambda style, text: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(pam_worker, "_broker", lambda broker_id: failing_broker)
    assert pam_worker._conversation_conv(1, msgs, response, ctypes.c_void_p(123)) == ffi.PAM_CONV_ERR


def test_inherited_socket_fd_validates_environment(monkeypatch):
    monkeypatch.setenv("WLDM_SOCKET_FD", "7")
    assert pam_worker.inherited_socket_fd() == 7

    monkeypatch.setenv("WLDM_SOCKET_FD", "bad")
    try:
        pam_worker.inherited_socket_fd()
    except RuntimeError as exc:
        assert "invalid or missing" in str(exc)
    else:
        raise AssertionError("non-integer fd should fail")

    monkeypatch.setenv("WLDM_SOCKET_FD", "-1")
    try:
        pam_worker.inherited_socket_fd()
    except RuntimeError as exc:
        assert "non-negative" in str(exc)
    else:
        raise AssertionError("negative fd should fail")


def test_run_auth_session_reports_ready_and_failures(monkeypatch):
    calls = []
    sock = SimpleNamespace(sendall=lambda data: calls.append(pam_worker_protocol.decode_message(data)))
    monkeypatch.setattr(ffi, "pam_handle_t", lambda: ctypes.c_void_p())
    monkeypatch.setattr(ffi.libpam, "pam_start", lambda service, user, conv, pamh_ref: ffi.PAM_SUCCESS)
    monkeypatch.setattr(ffi.libpam, "pam_authenticate", lambda pamh_arg, flags: ffi.PAM_SUCCESS)
    monkeypatch.setattr(ffi.libpam, "pam_acct_mgmt", lambda pamh_arg, flags: ffi.PAM_SUCCESS)
    monkeypatch.setattr(pam_worker.wldm.pam, "set_pam_item", lambda pamh_arg, item, value: calls.append((item, value)))
    monkeypatch.setattr(pam_worker.wldm.pam, "end_pam", lambda pamh_arg: calls.append(("end", pamh_arg)))

    assert pam_worker.run_auth_session(sock, "login", "alice", "/dev/tty7") == pam_worker.wldm.EX_SUCCESS
    assert calls[0] == (ffi.PAM_TTY, "/dev/tty7")
    assert calls[1] == {"v": 1, "kind": "ready"}
    assert calls[2][0] == "end"

    calls.clear()
    monkeypatch.setattr(ffi.libpam, "pam_authenticate", lambda pamh_arg, flags: ffi.PAM_CONV_ERR)
    monkeypatch.setattr(pam_worker.wldm.pam, "pam_error_str", lambda pamh_arg, rc: "bad auth")

    assert pam_worker.run_auth_session(sock, "login", "alice", "") == pam_worker.wldm.EX_FAILURE
    assert calls[0] == {"v": 1, "kind": "failed", "code": "auth_failed", "message": "Authentication failed."}


def test_run_auth_session_handles_pam_start_account_and_conversation_errors(monkeypatch):
    calls = []
    sock = SimpleNamespace(sendall=lambda data: calls.append(pam_worker_protocol.decode_message(data)))

    monkeypatch.setattr(ffi, "pam_handle_t", lambda: ctypes.c_void_p())
    monkeypatch.setattr(pam_worker.wldm.pam, "set_pam_item", lambda pamh_arg, item, value: calls.append((item, value)))
    monkeypatch.setattr(pam_worker.wldm.pam, "end_pam", lambda pamh_arg: calls.append(("end", pamh_arg)))
    monkeypatch.setattr(pam_worker.wldm.pam, "pam_error_str", lambda pamh_arg, rc: "bad news")

    monkeypatch.setattr(ffi.libpam, "pam_start", lambda service, user, conv, pamh_ref: ffi.PAM_ABORT)
    assert pam_worker.run_auth_session(sock, "login", "alice", "/dev/tty7") == pam_worker.wldm.EX_FAILURE
    assert calls[0] == {"v": 1, "kind": "failed", "code": "auth_failed", "message": "pam_start failed: 26 (bad news)"}
    assert calls[1][0] == "end"

    calls.clear()
    monkeypatch.setattr(ffi.libpam, "pam_start", lambda service, user, conv, pamh_ref: ffi.PAM_SUCCESS)
    monkeypatch.setattr(ffi.libpam, "pam_authenticate", lambda pamh_arg, flags: ffi.PAM_SUCCESS)
    monkeypatch.setattr(ffi.libpam, "pam_acct_mgmt", lambda pamh_arg, flags: ffi.PAM_ACCT_EXPIRED)
    assert pam_worker.run_auth_session(sock, "login", "alice", "") == pam_worker.wldm.EX_FAILURE
    assert calls[0] == {"v": 1, "kind": "failed", "code": "auth_failed", "message": "Account expired."}
    assert calls[1][0] == "end"

    calls.clear()
    monkeypatch.setattr(ffi.libpam, "pam_authenticate",
                        lambda pamh_arg, flags: (_ for _ in ()).throw(pam_worker.ConversationError("cancelled")))
    assert pam_worker.run_auth_session(sock, "login", "alice", "") == pam_worker.wldm.EX_FAILURE
    assert len(calls) == 1
    assert calls[0][0] == "end"


def test_user_facing_error_maps_common_pam_codes():
    assert pam_worker.user_facing_error("auth", ffi.PAM_AUTH_ERR) == "Authentication failed."
    assert pam_worker.user_facing_error("auth", ffi.PAM_MAXTRIES) == "Authentication failed."
    assert pam_worker.user_facing_error("acct", ffi.PAM_NEW_AUTHTOK_REQD) == "Password change required."
    assert pam_worker.user_facing_error("acct", ffi.PAM_ACCT_EXPIRED) == "Account expired."
    assert pam_worker.user_facing_error("acct", ffi.PAM_ABORT) == "Authentication service unavailable."


def test_failure_code_marks_only_retryable_auth_failures():
    assert pam_worker.failure_code("auth", ffi.PAM_AUTH_ERR) == "auth_retryable"
    assert pam_worker.failure_code("auth", ffi.PAM_MAXTRIES) == "auth_failed"
    assert pam_worker.failure_code("acct", ffi.PAM_ACCT_EXPIRED) == "auth_failed"


def test_cmd_main_reads_start_message_and_calls_run_auth_session(monkeypatch):
    left, right = socket.socketpair()
    calls = {}

    try:
        left.sendall(pam_worker_protocol.encode_message(pam_worker_protocol.new_start("login", "alice", "/dev/tty7")))
        right_fd = right.detach()
        monkeypatch.setattr(pam_worker, "inherited_socket_fd", lambda: right_fd)

        def fake_run_auth_session(sock, service, username, tty):
            calls["args"] = (service, username, tty)
            return 23

        monkeypatch.setattr(pam_worker, "run_auth_session", fake_run_auth_session)

        result = pam_worker.cmd_main(SimpleNamespace())
    finally:
        left.close()

    assert result == 23
    assert calls["args"] == ("login", "alice", "/dev/tty7")


def test_cmd_main_rejects_non_start_message(monkeypatch):
    left, right = socket.socketpair()

    try:
        left.sendall(pam_worker_protocol.encode_message(pam_worker_protocol.new_cancel()))
        right_fd = right.detach()
        monkeypatch.setattr(pam_worker, "inherited_socket_fd", lambda: right_fd)

        try:
            pam_worker.cmd_main(SimpleNamespace())
        except RuntimeError as exc:
            assert "expected start message" in str(exc)
        else:
            raise AssertionError("non-start message should fail")
    finally:
        left.close()


def test_cmd_main_rejects_closed_channel_before_start(monkeypatch):
    left, right = socket.socketpair()

    try:
        left.close()
        right_fd = right.detach()
        monkeypatch.setattr(pam_worker, "inherited_socket_fd", lambda: right_fd)

        try:
            pam_worker.cmd_main(SimpleNamespace())
        except RuntimeError as exc:
            assert "before start" in str(exc)
        else:
            raise AssertionError("missing start message should fail")
    finally:
        try:
            right.close()
        except OSError:
            pass
