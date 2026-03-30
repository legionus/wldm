# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026  Alexey Gladkov <legion@kernel.org>

import asyncio
import configparser
import json
import signal
import stat
from types import SimpleNamespace

import wldm.daemon
import wldm.protocol


class DummyReader:
    def __init__(self, lines):
        self.lines = iter(lines)

    async def readline(self):
        return next(self.lines, b"")


class DummyWriter:
    def __init__(self, peer_uid=32):
        self.lines = []
        self.closed = False
        self.waited = False
        self.peer_uid = peer_uid

    def write(self, data):
        self.lines.append(data.decode())

    async def drain(self):
        return None

    def close(self):
        self.closed = True

    async def wait_closed(self):
        self.waited = True

    def get_extra_info(self, name):
        if name != "socket":
            return None

        writer = self

        class DummySocket:
            def getsockopt(self, level, optname, buflen):
                return (0).to_bytes(4, "little") + writer.peer_uid.to_bytes(4, "little") + (0).to_bytes(4, "little")

        return DummySocket()


class DummyAsyncProc:
    def __init__(self, pid=1234, returncode=0):
        self.pid = pid
        self.returncode = returncode
        self.wait_calls = 0

    async def wait(self):
        self.wait_calls += 1
        return self.returncode


class DummyServer:
    def __init__(self):
        self.closed = False
        self.waited = False

    def close(self):
        self.closed = True

    async def wait_closed(self):
        self.waited = True


class DummyListener:
    def __init__(self, path="/tmp/wldm-test.sock"):
        self.path = path
        self.sock = object()
        self.closed = False

    def close(self):
        self.closed = True


def make_config(user="gdm",
                group="gdm",
                tty="7",
                command="cage -s -m last --",
                pam_service="system-login",
                max_restarts="3",
                user_sessions="yes",
                seat="seat0",
                socket_path="/tmp/wldm/greeter.sock",
                daemon_log="/tmp/wldm/daemon.log",
                greeter_log="/tmp/wldm/greeter.log"):
    cfg = configparser.ConfigParser()
    cfg["daemon"] = {
        "seat": seat,
        "socket-path": socket_path,
        "log-path": daemon_log,
        "poweroff-command": "systemctl poweroff",
        "reboot-command": "systemctl reboot",
    }
    cfg["greeter"] = {
        "user": user,
        "group": group,
        "tty": tty,
        "command": command,
        "pam-service": pam_service,
        "max-restarts": max_restarts,
        "user-sessions": user_sessions,
        "log-path": greeter_log,
    }
    return cfg


def test_verify_creds_requires_username_and_password(monkeypatch):
    monkeypatch.setattr(wldm.daemon.wldm.pam, "authenticate", lambda username, password: True)

    assert wldm.daemon.verify_creds({"username": "alice", "password": "secret"}) is True
    assert wldm.daemon.verify_creds({"username": "alice"}) is False


def test_verify_creds_returns_false_on_auth_exception(monkeypatch):
    monkeypatch.setattr(
        wldm.daemon.wldm.pam,
        "authenticate",
        lambda username, password: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    assert wldm.daemon.verify_creds({"username": "alice", "password": "secret"}) is False


def test_process_request_accepts_poweroff_and_reboot():
    poweroff = wldm.daemon.process_request(wldm.protocol.new_request(wldm.protocol.ACTION_POWEROFF, {}))
    reboot = wldm.daemon.process_request(wldm.protocol.new_request(wldm.protocol.ACTION_REBOOT, {}))

    assert poweroff.response["payload"] == {"accepted": True}
    assert poweroff.control_action == wldm.protocol.ACTION_POWEROFF
    assert reboot.response["payload"] == {"accepted": True}
    assert reboot.control_action == wldm.protocol.ACTION_REBOOT


def test_process_request_replies_with_bad_request_for_unknown_payload():
    outcome = wldm.daemon.process_request({})

    assert outcome.response == {
        "v": 1,
        "id": "",
        "type": "response",
        "action": "",
        "ok": False,
        "error": {"code": "bad_request", "message": "Malformed request"},
    }
    assert outcome.event is None


def test_process_request_does_not_start_session_for_failed_auth(monkeypatch):
    req = wldm.protocol.new_request(
        wldm.protocol.ACTION_AUTH,
        {"username": "alice", "password": "bad", "command": "ignored"},
    )

    monkeypatch.setattr(wldm.daemon, "verify_creds", lambda req: False)

    outcome = wldm.daemon.process_request(req)

    assert outcome.response["payload"] == {"verified": False}
    assert outcome.event is None
    assert outcome.session_username == ""


def test_process_request_starts_session_after_successful_auth(monkeypatch):
    req = wldm.protocol.new_request(
        wldm.protocol.ACTION_AUTH,
        {"username": "alice", "password": "secret", "command": "startplasma-wayland --debug"},
    )

    monkeypatch.setattr(wldm.daemon, "verify_creds", lambda req: True)

    outcome = wldm.daemon.process_request(req)

    assert outcome.response["payload"] == {"verified": True}
    assert outcome.event == {
        "v": 1,
        "type": "event",
        "event": wldm.protocol.EVENT_SESSION_STARTING,
        "payload": {"username": "alice", "command": "startplasma-wayland --debug"},
    }
    assert outcome.session_username == "alice"
    assert outcome.session_command == "startplasma-wayland --debug"


def test_process_request_replies_with_unknown_action_error():
    req = wldm.protocol.new_request("mystery", {})

    outcome = wldm.daemon.process_request(req)

    assert outcome.response["error"]["code"] == "unknown_action"


def test_greeter_socket_path_uses_env(monkeypatch):
    monkeypatch.setenv("WLDM_SOCKET", "/tmp/custom.sock")

    assert wldm.daemon.greeter_socket_path() == "/tmp/custom.sock"


def test_greeter_socket_path_uses_config_when_env_is_not_set(monkeypatch):
    monkeypatch.delenv("WLDM_SOCKET", raising=False)

    assert wldm.daemon.greeter_socket_path(make_config(socket_path="/tmp/from-config.sock")) == "/tmp/from-config.sock"


def test_create_greeter_listener_applies_permissions(monkeypatch):
    calls = []

    class FakeSocketListener:
        def __init__(self, path):
            self.path = path
            self.sock = object()

    class DummyContext:
        def __enter__(self):
            calls.append(("open_dir", "/tmp/wldm"))
            return 11

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(wldm.daemon, "SocketListener", FakeSocketListener)
    monkeypatch.setattr(wldm.daemon.wldm, "open_secure_directory", lambda path, mode=0o755: DummyContext())
    monkeypatch.setattr(wldm.daemon.os, "chown", lambda path, uid, gid: calls.append(("chown", path, uid, gid)))
    monkeypatch.setattr(wldm.daemon.os, "chmod", lambda path, mode: calls.append(("chmod", path, mode)))
    monkeypatch.setattr(
        wldm.daemon.os,
        "stat",
        lambda path, dir_fd=None, follow_symlinks=False: (_ for _ in ()).throw(FileNotFoundError()),
    )
    monkeypatch.setattr(wldm.daemon.pwd, "getpwnam", lambda user: SimpleNamespace(pw_uid=32))
    monkeypatch.setattr(wldm.daemon.grp, "getgrnam", lambda group: SimpleNamespace(gr_gid=32))

    listener = wldm.daemon.create_greeter_listener("gdm", "gdm", "/tmp/wldm/greeter.sock")

    assert listener.path == "/tmp/wldm/greeter.sock"
    assert ("open_dir", "/tmp/wldm") in calls
    assert ("chown", "/tmp/wldm/greeter.sock", 32, 32) in calls
    assert ("chmod", "/tmp/wldm/greeter.sock", 0o600) in calls


def test_create_greeter_listener_rejects_symlink(monkeypatch):
    class DummyContext:
        def __enter__(self):
            return 11

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(wldm.daemon.wldm, "open_secure_directory", lambda path, mode=0o755: DummyContext())
    monkeypatch.setattr(
        wldm.daemon.os,
        "stat",
        lambda path, dir_fd=None, follow_symlinks=False: SimpleNamespace(st_mode=stat.S_IFLNK),
    )

    try:
        wldm.daemon.create_greeter_listener("gdm", "gdm", "/tmp/wldm/greeter.sock")
    except RuntimeError as exc:
        assert "non-socket" in str(exc)
    else:
        raise AssertionError("create_greeter_listener() should reject symlinks")


def test_greeter_command_uses_configured_launcher():
    cfg = make_config(command="labwc --")

    assert wldm.daemon.greeter_command(cfg, "/srv/wldm/wldm.sh") == [
        "labwc",
        "--",
        "/srv/wldm/wldm.sh",
        "greeter",
    ]


def test_control_command_uses_configured_system_commands():
    cfg = make_config()
    cfg["daemon"]["poweroff-command"] = "do-poweroff --now"
    cfg["daemon"]["reboot-command"] = "do-reboot --cold"

    assert wldm.daemon.control_command(cfg, wldm.protocol.ACTION_POWEROFF) == ["do-poweroff", "--now"]
    assert wldm.daemon.control_command(cfg, wldm.protocol.ACTION_REBOOT) == ["do-reboot", "--cold"]


def test_send_message_writes_encoded_line():
    writer = DummyWriter()

    result = asyncio.run(wldm.daemon.send_message(writer, {"ping": True}))

    assert result is True
    assert writer.lines == ['{"ping": true}\n']


def test_handle_request_async_starts_session_after_auth(monkeypatch):
    state = wldm.daemon.DaemonState("/srv/wldm/wldm.sh", 3)
    state.greeter_writer = DummyWriter()
    req = wldm.protocol.new_request(
        wldm.protocol.ACTION_AUTH,
        {"username": "alice", "password": "secret", "command": "startplasma-wayland --debug"},
    )
    proc = DummyAsyncProc(pid=777, returncode=0)
    task_calls = []

    monkeypatch.setattr(wldm.daemon, "verify_creds", lambda payload: True)

    async def fake_create_subprocess_exec(*cmd, env=None):
        assert cmd == (
            "/srv/wldm/wldm.sh",
            "session",
            "--",
            "alice",
            "startplasma-wayland",
            "--debug",
        )
        assert env["WLDM_SEAT"] == "seat0"
        return proc

    async def fake_exec(*cmd, env=None, start_new_session=False):
        assert start_new_session is False
        return await fake_create_subprocess_exec(*cmd, env=env)

    monkeypatch.setattr(wldm.daemon.asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr(wldm.daemon, "track_session_task", lambda state, task: task_calls.append((state, task)))

    asyncio.run(wldm.daemon.handle_request_async(state, req, make_config()))

    assert 777 in state.active_sessions or task_calls
    assert any(
        json.loads(line).get("event") == wldm.protocol.EVENT_SESSION_STARTING
        for line in state.greeter_writer.lines
    )


def test_handle_request_async_runs_control_command(monkeypatch):
    state = wldm.daemon.DaemonState("/srv/wldm/wldm.sh", 3)
    state.greeter_writer = DummyWriter()
    req = wldm.protocol.new_request(wldm.protocol.ACTION_POWEROFF, {})
    calls = {}
    proc = DummyAsyncProc(pid=888, returncode=0)

    async def fake_create_subprocess_exec(*cmd, env=None, start_new_session=False):
        calls["cmd"] = cmd
        calls["env"] = env
        return proc

    cfg = make_config()
    cfg["daemon"]["poweroff-command"] = "do-poweroff --now"
    monkeypatch.setattr(wldm.daemon.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    asyncio.run(wldm.daemon.handle_request_async(state, req, cfg))

    assert calls["cmd"] == ("do-poweroff", "--now")
    assert json.loads(state.greeter_writer.lines[0])["payload"] == {"accepted": True}


def test_send_session_finished_switches_back_to_greeter_tty(monkeypatch):
    state = wldm.daemon.DaemonState("/srv/wldm/wldm.sh", 3)
    state.greeter_writer = DummyWriter()
    state.console = 77
    state.greeter_tty = 7
    proc = DummyAsyncProc(pid=333, returncode=0)
    state.active_sessions[333] = proc
    changes = []

    monkeypatch.setattr(wldm.daemon.wldm.tty, "change", lambda console, tty: changes.append((console, tty)) or True)

    asyncio.run(wldm.daemon.send_session_finished(state, proc))

    assert changes == [(77, 7)]
    assert 333 not in state.active_sessions
    assert any(
        json.loads(line).get("event") == wldm.protocol.EVENT_SESSION_FINISHED
        for line in state.greeter_writer.lines
    )


def test_handle_greeter_client_marks_greeter_ready(monkeypatch):
    state = wldm.daemon.DaemonState("/srv/wldm/wldm.sh", 3, greeter_uid=32)
    writer = DummyWriter()
    req = wldm.protocol.new_request(wldm.protocol.ACTION_REBOOT, {})
    reader = DummyReader([(wldm.protocol.encode_message(req) + "\n").encode(), b""])
    calls = []

    async def fake_handle_request_async(state_arg, req_arg, cfg_arg=None):
        calls.append((state_arg, req_arg, cfg_arg))

    monkeypatch.setattr(wldm.daemon, "handle_request_async", fake_handle_request_async)

    cfg = make_config()
    asyncio.run(wldm.daemon.handle_greeter_client(state, reader, writer, cfg))

    assert state.greeter_ready is True
    assert calls[0][1]["action"] == wldm.protocol.ACTION_REBOOT
    assert calls[0][2] is cfg
    assert writer.closed is True


def test_handle_greeter_client_rejects_unexpected_peer_uid(monkeypatch):
    state = wldm.daemon.DaemonState("/srv/wldm/wldm.sh", 3, greeter_uid=32)
    writer = DummyWriter(peer_uid=0)
    reader = DummyReader([b""])
    criticals = []

    monkeypatch.setattr(wldm.daemon.logger, "critical",
                        lambda msg, *args: criticals.append(msg % args if args else msg))

    asyncio.run(wldm.daemon.handle_greeter_client(state, reader, writer, make_config()))

    assert state.greeter_writer is None
    assert writer.closed is True
    assert any("unexpected uid 0" in message for message in criticals)


def test_start_greeter_passes_socket_env(monkeypatch):
    state = wldm.daemon.DaemonState("/srv/wldm/wldm.sh", 3, seat="seat9")
    cfg = make_config(command="labwc --", greeter_log="/tmp/custom-greeter.log", user_sessions="no")
    calls = {}
    proc = DummyAsyncProc(pid=4321, returncode=0)

    async def fake_create_subprocess_exec(*cmd, env=None, start_new_session=False):
        calls["cmd"] = cmd
        calls["env"] = env
        return proc

    monkeypatch.setattr(wldm.daemon.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    result = asyncio.run(wldm.daemon.start_greeter(state, cfg, 7, "/tmp/wldm/greeter.sock"))

    assert result is proc
    assert calls["cmd"][:8] == (
        "/srv/wldm/wldm.sh",
        "greeter-session",
        "--tty",
        "7",
        "--pam-service",
        "system-login",
        "gdm",
        "gdm",
    )
    assert calls["cmd"][8:] == (
        "labwc",
        "--",
        "/srv/wldm/wldm.sh",
        "greeter",
    )
    assert calls["env"]["WLDM_SOCKET"] == "/tmp/wldm/greeter.sock"
    assert calls["env"]["WLDM_SEAT"] == "seat9"
    assert calls["env"]["WLDM_GREETER_STDERR_LOG"] == "/tmp/custom-greeter.log"
    assert calls["env"]["WLDM_GREETER_USER_SESSIONS"] == "no"


def test_terminate_process_tree_sends_signals_to_process_group(monkeypatch):
    proc = DummyAsyncProc(pid=4321, returncode=None)
    signals = []

    monkeypatch.setattr(wldm.daemon.os, "killpg", lambda pid, sig: signals.append((pid, sig)))

    asyncio.run(wldm.daemon.terminate_process_tree(proc, "the greeter"))

    assert signals == [(4321, signal.SIGTERM)]
    assert proc.wait_calls == 1


def test_cleanup_async_terminates_greeter_and_sessions(monkeypatch):
    greeter = DummyAsyncProc(pid=11, returncode=None)
    session = DummyAsyncProc(pid=22, returncode=None)
    state = wldm.daemon.DaemonState("/srv/wldm/wldm.sh", 3)
    state.greeter_proc = greeter
    state.active_sessions = {22: session}
    calls = []

    async def fake_terminate(proc, name, timeout=5.0):
        calls.append((proc.pid, name, timeout))

    monkeypatch.setattr(wldm.daemon, "terminate_process_tree", fake_terminate)

    asyncio.run(wldm.daemon.cleanup_async(state))

    assert calls == [
        (11, "the greeter", 5.0),
        (22, "user session", 5.0),
    ]


def test_wait_for_stop_or_process_returns_true_when_stop_is_set():
    proc = DummyAsyncProc(pid=11, returncode=0)
    stop_event = asyncio.Event()
    stop_event.set()

    result = asyncio.run(wldm.daemon.wait_for_stop_or_process(proc, stop_event))

    assert result is True


def test_wait_for_stop_or_process_returns_false_when_process_exits():
    proc = DummyAsyncProc(pid=11, returncode=0)
    stop_event = asyncio.Event()

    result = asyncio.run(wldm.daemon.wait_for_stop_or_process(proc, stop_event))

    assert result is False


def test_run_daemon_async_fails_when_console_is_unavailable(monkeypatch):
    monkeypatch.setattr(wldm.daemon.wldm.config, "read_config", lambda: make_config())
    monkeypatch.setattr(wldm.daemon.wldm.tty, "open_console", lambda: None)

    result = asyncio.run(wldm.daemon.run_daemon_async(SimpleNamespace(tty=None)))

    assert result == wldm.daemon.wldm.EX_FAILURE


def test_run_daemon_async_fails_when_tty_switch_fails(monkeypatch):
    closed = []

    monkeypatch.setattr(wldm.daemon.wldm.config, "read_config", lambda: make_config(tty="7"))
    monkeypatch.setattr(wldm.daemon.wldm.tty, "open_console", lambda: 88)
    monkeypatch.setattr(wldm.daemon.wldm.tty, "change", lambda console, tty: False)
    monkeypatch.setattr(wldm.daemon.os, "close", lambda fd: closed.append(fd))

    result = asyncio.run(wldm.daemon.run_daemon_async(SimpleNamespace(tty=None)))

    assert result == wldm.daemon.wldm.EX_FAILURE
    assert closed == [88]


def test_run_daemon_async_stops_after_configured_failed_greeter_starts(monkeypatch):
    listener = DummyListener()
    server = DummyServer()
    greeters = [DummyAsyncProc(pid=1, returncode=5), DummyAsyncProc(pid=2, returncode=6)]
    sleeps = []

    async def fake_start_unix_server(handler, sock=None):
        return server

    async def fake_start_greeter(state, cfg, greeter_tty, socket_path):
        return greeters.pop(0)

    async def fake_cleanup_async(state):
        return None

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr(wldm.daemon.wldm.config, "read_config", lambda: make_config(max_restarts="2"))
    monkeypatch.setattr(wldm.daemon.wldm.tty, "open_console", lambda: 88)
    monkeypatch.setattr(wldm.daemon.wldm.tty, "change", lambda console, tty: True)
    monkeypatch.setattr(wldm.daemon.pwd, "getpwnam", lambda user: SimpleNamespace(pw_uid=32))
    monkeypatch.setattr(wldm.daemon, "create_greeter_listener", lambda user, group, path: listener)
    monkeypatch.setattr(wldm.daemon.asyncio, "start_unix_server", fake_start_unix_server)
    monkeypatch.setattr(wldm.daemon, "start_greeter", fake_start_greeter)
    monkeypatch.setattr(wldm.daemon, "cleanup_async", fake_cleanup_async)
    monkeypatch.setattr(wldm.daemon.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(wldm.daemon.os, "close", lambda fd: None)

    result = asyncio.run(wldm.daemon.run_daemon_async(SimpleNamespace(tty=None)))

    assert result == wldm.daemon.wldm.EX_FAILURE
    assert sleeps == [1]
    assert server.closed is True
    assert server.waited is True
    assert listener.closed is True


def test_run_daemon_async_cleans_up_after_stop_signal(monkeypatch):
    listener = DummyListener()
    server = DummyServer()
    cleanup_calls = []
    closed = []
    stop_event = asyncio.Event()

    async def fake_start_unix_server(handler, sock=None):
        return server

    async def fake_start_greeter(state, cfg, greeter_tty, socket_path):
        return DummyAsyncProc(pid=1, returncode=None)

    async def fake_wait_for_stop_or_process(proc, event):
        assert event is stop_event
        stop_event.set()
        return True

    async def fake_cleanup_async(state):
        cleanup_calls.append(state)

    monkeypatch.setattr(wldm.daemon.wldm.config, "read_config", lambda: make_config())
    monkeypatch.setattr(wldm.daemon.wldm.tty, "open_console", lambda: 88)
    monkeypatch.setattr(wldm.daemon.wldm.tty, "change", lambda console, tty: True)
    monkeypatch.setattr(wldm.daemon.pwd, "getpwnam", lambda user: SimpleNamespace(pw_uid=32))
    monkeypatch.setattr(wldm.daemon, "create_greeter_listener", lambda user, group, path: listener)
    monkeypatch.setattr(wldm.daemon.asyncio, "start_unix_server", fake_start_unix_server)
    monkeypatch.setattr(wldm.daemon, "start_greeter", fake_start_greeter)
    monkeypatch.setattr(wldm.daemon, "wait_for_stop_or_process", fake_wait_for_stop_or_process)
    monkeypatch.setattr(wldm.daemon, "cleanup_async", fake_cleanup_async)
    monkeypatch.setattr(wldm.daemon, "install_stop_handlers", lambda loop, event: stop_event.set() if event is stop_event else None)
    monkeypatch.setattr(wldm.daemon, "remove_stop_handlers", lambda loop: None)
    monkeypatch.setattr(wldm.daemon.os, "close", lambda fd: closed.append(fd))
    monkeypatch.setattr(wldm.daemon.asyncio, "Event", lambda: stop_event)

    result = asyncio.run(wldm.daemon.run_daemon_async(SimpleNamespace(tty=None)))

    assert result == wldm.daemon.wldm.EX_SUCCESS
    assert len(cleanup_calls) == 1
    assert server.closed is True
    assert server.waited is True
    assert listener.closed is True
    assert closed == [88]


def test_run_daemon_async_cleans_up_on_cancellation(monkeypatch):
    listener = DummyListener()
    server = DummyServer()
    cleanup_calls = []
    closed = []

    async def fake_start_unix_server(handler, sock=None):
        return server

    async def fake_start_greeter(state, cfg, greeter_tty, socket_path):
        raise asyncio.CancelledError()

    async def fake_cleanup_async(state):
        cleanup_calls.append(state)

    monkeypatch.setattr(wldm.daemon.wldm.config, "read_config", lambda: make_config())
    monkeypatch.setattr(wldm.daemon.wldm.tty, "open_console", lambda: 88)
    monkeypatch.setattr(wldm.daemon.wldm.tty, "change", lambda console, tty: True)
    monkeypatch.setattr(wldm.daemon.pwd, "getpwnam", lambda user: SimpleNamespace(pw_uid=32))
    monkeypatch.setattr(wldm.daemon, "create_greeter_listener", lambda user, group, path: listener)
    monkeypatch.setattr(wldm.daemon.asyncio, "start_unix_server", fake_start_unix_server)
    monkeypatch.setattr(wldm.daemon, "start_greeter", fake_start_greeter)
    monkeypatch.setattr(wldm.daemon, "cleanup_async", fake_cleanup_async)
    monkeypatch.setattr(wldm.daemon.os, "close", lambda fd: closed.append(fd))

    try:
        asyncio.run(wldm.daemon.run_daemon_async(SimpleNamespace(tty=None)))
    except asyncio.CancelledError:
        pass
    else:
        raise AssertionError("run_daemon_async() must propagate CancelledError")

    assert len(cleanup_calls) == 2
    assert server.closed is True
    assert server.waited is True
    assert listener.closed is True
    assert closed == [88]


def test_cmd_main_enables_daemon_file_log_when_configured(monkeypatch):
    cfg = make_config(daemon_log="/tmp/wldm/daemon.log")
    calls = []

    monkeypatch.setattr(wldm.daemon.wldm.config, "read_config", lambda: cfg)
    monkeypatch.setattr(
        wldm.daemon.wldm,
        "setup_file_logger",
        lambda logger, level, fmt, path: calls.append((logger.name, level, fmt, path)) or logger,
    )
    def fake_asyncio_run(coro):
        coro.close()
        return wldm.daemon.wldm.EX_SUCCESS

    monkeypatch.setattr(wldm.daemon.asyncio, "run", fake_asyncio_run)

    result = wldm.daemon.cmd_main(SimpleNamespace())

    assert result == wldm.daemon.wldm.EX_SUCCESS
    assert calls == [("wldm", wldm.daemon.logger.level, "[%(asctime)s] %(message)s", "/tmp/wldm/daemon.log")]
