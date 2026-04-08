# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026  Alexey Gladkov <legion@kernel.org>

import asyncio
from types import SimpleNamespace

import wldm.daemon_auth
import wldm.greeter_protocol as greeter_protocol
import wldm.pam_worker_protocol as pam_worker_protocol
from wldm.secret import SecretBytes
from tests.helpers_daemon import DummyProc, DummyWriter, make_worker_auth_session


def patch_start_auth_runtime(monkeypatch, *, proc, writer, calls, worker_message):
    async def fake_create_subprocess_exec(*cmd, env=None, pass_fds=()):
        calls["cmd"] = cmd
        calls["env"] = env
        calls["pass_fds"] = pass_fds
        return proc

    async def fake_open_connection(sock=None):
        calls["sock"] = sock
        return SimpleNamespace(), writer

    async def fake_read_auth_worker_message(session):
        calls["session"] = session
        return worker_message

    monkeypatch.setattr(wldm.daemon_auth.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr(wldm.daemon_auth.asyncio, "open_connection", fake_open_connection)
    monkeypatch.setattr(wldm.daemon_auth, "read_auth_worker_message", fake_read_auth_worker_message)


def test_tty_device_path_formats_positive_tty_number():
    assert wldm.daemon_auth.tty_device_path(7) == "/dev/tty7"
    assert wldm.daemon_auth.tty_device_path(0) == ""


def test_conversation_response_from_worker_maps_messages():
    req = greeter_protocol.new_request(greeter_protocol.ACTION_CREATE_SESSION, {})

    prompt = wldm.daemon_auth.conversation_response_from_worker(
        req,
        pam_worker_protocol.new_prompt("secret", "Password:"),
    )
    ready = wldm.daemon_auth.conversation_response_from_worker(
        req,
        pam_worker_protocol.new_ready(),
    )
    failed = wldm.daemon_auth.conversation_response_from_worker(
        req,
        pam_worker_protocol.new_failed("auth_failed", "nope"),
    )

    assert prompt["payload"] == {"state": "pending", "message": {"style": "secret", "text": "Password:"}}
    assert ready["payload"] == {"state": "ready"}
    assert failed["error"] == {"code": "auth_failed", "message": "nope"}


def test_conversation_response_from_worker_rejects_unknown_kind():
    req = greeter_protocol.new_request(greeter_protocol.ACTION_CREATE_SESSION, {})

    try:
        wldm.daemon_auth.conversation_response_from_worker(req, {"kind": "mystery"})
    except RuntimeError as exc:
        assert "unexpected PAM worker message" in str(exc)
    else:
        raise AssertionError("unexpected worker message should fail")


def test_continue_auth_session_writes_answer_and_clears_secret(monkeypatch):
    auth_session = make_worker_auth_session(wldm.daemon_auth)
    response = SecretBytes(b"secret")

    async def fake_read_auth_worker_message(session):
        assert session is auth_session
        return pam_worker_protocol.new_ready()

    monkeypatch.setattr(wldm.daemon_auth, "read_auth_worker_message", fake_read_auth_worker_message)

    message = asyncio.run(wldm.daemon_auth.continue_auth_session(auth_session, response))

    assert message == {"v": 1, "kind": "ready"}
    assert response.as_bytes() == b""
    decoded = pam_worker_protocol.decode_message(auth_session.writer.lines[0])
    assert decoded["kind"] == "answer"
    assert decoded["response"].as_bytes() == b"secret"


def test_stop_auth_session_can_send_cancel_and_close_writer(monkeypatch):
    auth_session = make_worker_auth_session(wldm.daemon_auth)
    calls = []

    async def fake_terminate_process(proc, name, timeout=5.0):
        calls.append((proc.pid, name, timeout))

    monkeypatch.setattr(wldm.daemon_auth, "terminate_process", fake_terminate_process)

    asyncio.run(wldm.daemon_auth.stop_auth_session(auth_session, send_cancel=True))

    assert auth_session.writer.closed is True
    assert auth_session.writer.waited is True
    assert calls == [(321, "pam-worker", 5.0)]
    assert pam_worker_protocol.decode_message(auth_session.writer.lines[0]) == {"v": 1, "kind": "cancel"}


def test_read_auth_worker_message_delegates_to_protocol(monkeypatch):
    auth_session = make_worker_auth_session(wldm.daemon_auth)

    async def fake_read_message_async(reader):
        assert reader is auth_session.reader
        return {"v": 1, "kind": "ready"}

    monkeypatch.setattr(wldm.daemon_auth.pam_worker_protocol, "read_message_async", fake_read_message_async)

    assert asyncio.run(wldm.daemon_auth.read_auth_worker_message(auth_session)) == {"v": 1, "kind": "ready"}


def test_start_auth_session_starts_worker_and_sends_start_message(monkeypatch):
    proc = DummyProc(pid=999, returncode=0)
    writer = DummyWriter()
    calls = {}
    patch_start_auth_runtime(
        monkeypatch,
        proc=proc,
        writer=writer,
        calls=calls,
        worker_message=pam_worker_protocol.new_prompt("visible", "OTP:"),
    )

    auth_session, message = asyncio.run(
        wldm.daemon_auth.start_auth_session(
            ["/usr/bin/python3", "-m", "wldm.command"],
            "/dev/tty7",
            "alice",
        )
    )

    assert calls["cmd"] == ("/usr/bin/python3", "-m", "wldm.command", "pam-worker")
    assert calls["env"]["WLDM_SOCKET_FD"] == str(calls["pass_fds"][0])
    assert auth_session.proc is proc
    assert message == {"v": 1, "kind": "prompt", "style": "visible", "text": "OTP:"}
    decoded = pam_worker_protocol.decode_message(writer.lines[0])
    assert decoded == {"v": 1, "kind": "start", "service": "login", "username": "alice", "tty": "/dev/tty7"}


def test_terminate_process_escalates_to_kill_after_timeout(monkeypatch):
    calls = []

    class SlowProc:
        pid = 700
        returncode = None

        def terminate(self):
            calls.append("terminate")

        def kill(self):
            calls.append("kill")

        async def wait(self):
            calls.append("wait")
            return 0

    async def fake_wait_for(awaitable, timeout):
        awaitable.close()
        calls.append(("timeout", timeout))
        raise asyncio.TimeoutError()

    monkeypatch.setattr(wldm.daemon_auth.asyncio, "wait_for", fake_wait_for)

    asyncio.run(wldm.daemon_auth.terminate_process(SlowProc(), "pam-worker"))

    assert calls == ["terminate", ("timeout", 5.0), "kill", "wait"]
