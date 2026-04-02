# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026  Alexey Gladkov <legion@kernel.org>

import asyncio
import signal
import stat
from types import SimpleNamespace

import wldm.daemon
import wldm.config
import wldm.inifile
import wldm.pam
import wldm.protocol
import wldm.secret
import wldm.state
import wldm.tty


class DummyReader:
    def __init__(self, chunks):
        self.chunks = iter(chunks)

    async def readexactly(self, size):
        chunk = next(self.chunks, b"")
        if len(chunk) == size:
            return chunk
        partial = chunk[:size]
        raise asyncio.IncompleteReadError(partial=partial, expected=size)


class DummyWriter:
    def __init__(self, peer_uid=32):
        self.lines = []
        self.closed = False
        self.waited = False
        self.peer_uid = peer_uid

    def write(self, data):
        self.lines.append(data)

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
                theme="default",
                session_dirs="/usr/share/wayland-sessions",
                user_session_dir=".local/share/wayland-sessions",
                command="cage -s -m last --",
                pam_service="system-login",
                max_restarts="3",
                user_sessions="yes",
                seat="seat0",
                socket_path="/tmp/wldm/greeter.sock",
                state_dir="",
                daemon_log="/tmp/wldm/daemon.log",
                greeter_log="/tmp/wldm/greeter.log"):
    return wldm.inifile.IniFile({
        "daemon": {
            "seat": seat,
            "socket-path": socket_path,
            "state-dir": state_dir,
            "log-path": daemon_log,
            "poweroff-command": "systemctl poweroff",
            "reboot-command": "systemctl reboot",
            "suspend-command": "",
            "hibernate-command": "",
        },
        "greeter": {
            "user": user,
            "group": group,
            "tty": tty,
            "theme": theme,
            "session-dirs": session_dirs,
            "user-session-dir": user_session_dir,
            "command": command,
            "pam-service": pam_service,
            "max-restarts": max_restarts,
            "user-sessions": user_sessions,
            "log-path": greeter_log,
        },
    })


def test_verify_creds_requires_username_and_password(monkeypatch):
    monkeypatch.setattr(wldm.pam, "authenticate", lambda username, password: True)

    assert wldm.daemon.verify_creds(wldm.secret.SecretBytes(b"alice"), wldm.secret.SecretBytes(b"secret")) is True
    assert wldm.daemon.verify_creds(wldm.secret.SecretBytes(b"alice"), wldm.secret.SecretBytes()) is False


def test_verify_creds_returns_false_on_auth_exception(monkeypatch):
    monkeypatch.setattr(
        wldm.pam,
        "authenticate",
        lambda username, password: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    assert wldm.daemon.verify_creds(wldm.secret.SecretBytes(b"alice"), wldm.secret.SecretBytes(b"secret")) is False


def test_process_request_accepts_poweroff_and_reboot():
    cfg = make_config()
    cfg["daemon"]["poweroff-command"] = "do-poweroff"
    cfg["daemon"]["reboot-command"] = "do-reboot"
    cfg["daemon"]["suspend-command"] = "do-suspend"
    cfg["daemon"]["hibernate-command"] = "do-hibernate"

    poweroff = wldm.daemon.process_request(wldm.protocol.new_request(wldm.protocol.ACTION_POWEROFF, {}), cfg)
    reboot = wldm.daemon.process_request(wldm.protocol.new_request(wldm.protocol.ACTION_REBOOT, {}), cfg)
    suspend = wldm.daemon.process_request(wldm.protocol.new_request(wldm.protocol.ACTION_SUSPEND, {}), cfg)
    hibernate = wldm.daemon.process_request(wldm.protocol.new_request(wldm.protocol.ACTION_HIBERNATE, {}), cfg)

    assert poweroff.response["payload"] == {"accepted": True}
    assert poweroff.control_action == wldm.protocol.ACTION_POWEROFF
    assert reboot.response["payload"] == {"accepted": True}
    assert reboot.control_action == wldm.protocol.ACTION_REBOOT
    assert suspend.response["payload"] == {"accepted": True}
    assert suspend.control_action == wldm.protocol.ACTION_SUSPEND
    assert hibernate.response["payload"] == {"accepted": True}
    assert hibernate.control_action == wldm.protocol.ACTION_HIBERNATE


def test_process_request_rejects_disabled_control_actions():
    cfg = make_config()

    outcome = wldm.daemon.process_request(wldm.protocol.new_request(wldm.protocol.ACTION_SUSPEND, {}), cfg)

    assert outcome.response["error"]["code"] == "action_disabled"


def test_process_request_replies_with_bad_request_for_unknown_payload():
    outcome = wldm.daemon.process_request({}, make_config())

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
        {
            "username": wldm.secret.SecretBytes(b"alice"),
            "password": wldm.secret.SecretBytes(b"bad"),
            "command": "ignored",
            "desktop_names": ["sway"],
        },
    )

    monkeypatch.setattr(wldm.daemon, "verify_creds", lambda username, password: False)

    outcome = wldm.daemon.process_request(req, make_config())

    assert outcome.response["payload"] == {"verified": False}
    assert isinstance(req["payload"]["username"], wldm.secret.SecretBytes)
    assert req["payload"]["username"].as_bytes() == b""
    assert isinstance(req["payload"]["password"], wldm.secret.SecretBytes)
    assert req["payload"]["password"].as_bytes() == b""
    assert outcome.event is None
    assert outcome.session_username == ""


def test_process_request_starts_session_after_successful_auth(monkeypatch):
    req = wldm.protocol.new_request(
        wldm.protocol.ACTION_AUTH,
        {
            "username": wldm.secret.SecretBytes(b"alice"),
            "password": wldm.secret.SecretBytes(b"secret"),
            "command": "startplasma-wayland --debug",
            "desktop_names": ["plasma", "kde"],
        },
    )

    monkeypatch.setattr(wldm.daemon, "verify_creds", lambda username, password: True)

    outcome = wldm.daemon.process_request(req, make_config())

    assert outcome.response["payload"] == {"verified": True}
    assert isinstance(req["payload"]["username"], wldm.secret.SecretBytes)
    assert req["payload"]["username"].as_bytes() == b""
    assert isinstance(req["payload"]["password"], wldm.secret.SecretBytes)
    assert req["payload"]["password"].as_bytes() == b""
    assert outcome.event == {
        "v": 1,
        "type": "event",
        "event": wldm.protocol.EVENT_SESSION_STARTING,
        "payload": {"command": "startplasma-wayland --debug", "desktop_names": ["plasma", "kde"]},
    }
    assert outcome.session_username == "alice"
    assert outcome.session_command == "startplasma-wayland --debug"
    assert outcome.session_desktop_names == ["plasma", "kde"]


def test_process_request_preserves_username_when_auth_clears_secret(monkeypatch):
    req = wldm.protocol.new_request(
        wldm.protocol.ACTION_AUTH,
        {
            "username": wldm.secret.SecretBytes(b"alice"),
            "password": wldm.secret.SecretBytes(b"secret"),
            "command": "sway",
            "desktop_names": ["sway"],
        },
    )

    def fake_verify_creds(username, password):
        username.clear()
        password.clear()
        return True

    monkeypatch.setattr(wldm.daemon, "verify_creds", fake_verify_creds)

    outcome = wldm.daemon.process_request(req, make_config())

    assert outcome.session_username == "alice"


def test_process_request_replies_with_unknown_action_error():
    req = wldm.protocol.new_request("mystery", {})

    outcome = wldm.daemon.process_request(req, make_config())

    assert outcome.response["error"]["code"] == "unknown_action"


def test_greeter_socket_path_uses_env(monkeypatch):
    monkeypatch.setenv("WLDM_SOCKET", "/tmp/custom.sock")

    assert wldm.daemon.greeter_socket_path() == "/tmp/custom.sock"


def test_greeter_socket_path_uses_config_when_env_is_not_set(monkeypatch):
    monkeypatch.delenv("WLDM_SOCKET", raising=False)

    assert wldm.daemon.greeter_socket_path(make_config(socket_path="/tmp/from-config.sock")) == "/tmp/from-config.sock"


def test_load_last_session_reads_state_file(tmp_path):
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / wldm.state.LAST_SESSION_FILE).write_text(
        "[session]\nusername = alice\ncommand = sway --debug\n",
        encoding="utf-8",
    )

    assert wldm.state.load_last_session(str(state_dir)) == ("alice", "sway --debug")


def test_save_last_session_writes_state_file(tmp_path):
    state_dir = tmp_path / "state"

    wldm.state.save_last_session(str(state_dir), "alice", "labwc")

    assert (state_dir / wldm.state.LAST_SESSION_FILE).read_text(encoding="utf-8") == (
        "[session]\nusername = alice\ncommand = labwc\n"
    )


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
    prefix = ["/usr/bin/python3", "/srv/wldm/src/wldm/command.py"]

    assert wldm.daemon.greeter_command(cfg, prefix) == [
        "labwc",
        "--",
        "/usr/bin/python3",
        "/srv/wldm/src/wldm/command.py",
        "greeter",
    ]


def test_control_command_uses_configured_system_commands():
    cfg = make_config()
    cfg["daemon"]["poweroff-command"] = "do-poweroff --now"
    cfg["daemon"]["reboot-command"] = "do-reboot --cold"
    cfg["daemon"]["suspend-command"] = "do-suspend"
    cfg["daemon"]["hibernate-command"] = "do-hibernate --deep"

    assert wldm.daemon.control_command(cfg, wldm.protocol.ACTION_POWEROFF) == ["do-poweroff", "--now"]
    assert wldm.daemon.control_command(cfg, wldm.protocol.ACTION_REBOOT) == ["do-reboot", "--cold"]
    assert wldm.daemon.control_command(cfg, wldm.protocol.ACTION_SUSPEND) == ["do-suspend"]
    assert wldm.daemon.control_command(cfg, wldm.protocol.ACTION_HIBERNATE) == ["do-hibernate", "--deep"]


def test_configured_power_actions_only_includes_enabled_actions():
    cfg = make_config()
    cfg["daemon"]["suspend-command"] = "do-suspend"

    assert wldm.daemon.configured_power_actions(cfg) == [
        wldm.protocol.ACTION_POWEROFF,
        wldm.protocol.ACTION_REBOOT,
        wldm.protocol.ACTION_SUSPEND,
    ]


def test_send_message_writes_encoded_line():
    writer = DummyWriter()
    message = wldm.protocol.new_request(wldm.protocol.ACTION_REBOOT, {})

    result = asyncio.run(wldm.daemon.send_message(writer, message))

    assert result is True
    assert writer.lines == [wldm.protocol.encode_message(message)]


def test_handle_request_async_starts_session_after_auth(monkeypatch):
    state = wldm.daemon.DaemonState(["/usr/bin/python3", "/srv/wldm/src/wldm/command.py"], 3)
    state.greeter_writer = DummyWriter()
    req = wldm.protocol.new_request(
        wldm.protocol.ACTION_AUTH,
        {
            "username": wldm.secret.SecretBytes(b"alice"),
            "password": wldm.secret.SecretBytes(b"secret"),
            "command": "startplasma-wayland --debug",
            "desktop_names": ["plasma", "kde"],
        },
    )
    proc = DummyAsyncProc(pid=777, returncode=0)
    task_calls = []

    monkeypatch.setattr(wldm.daemon, "verify_creds", lambda username, password: True)

    async def fake_create_subprocess_exec(*cmd, env=None):
        assert cmd == (
            "/usr/bin/python3",
            "/srv/wldm/src/wldm/command.py",
            "user-session",
            "--",
            "alice",
            "startplasma-wayland",
            "--debug",
        )
        assert env["WLDM_SEAT"] == "seat0"
        assert env["WLDM_SESSION_DESKTOP_NAMES"] == "plasma:kde"
        return proc

    async def fake_exec(*cmd, env=None, start_new_session=False):
        assert start_new_session is False
        return await fake_create_subprocess_exec(*cmd, env=env)

    monkeypatch.setattr(wldm.daemon.asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr(wldm.daemon, "track_session_task", lambda state, task: task_calls.append((state, task)))

    asyncio.run(wldm.daemon.handle_request_async(state, req, make_config()))

    assert 777 in state.active_sessions or task_calls
    assert any(
        wldm.protocol.decode_message(line).get("event") == wldm.protocol.EVENT_SESSION_STARTING
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
    assert wldm.protocol.decode_message(state.greeter_writer.lines[0])["payload"] == {"accepted": True}


def test_send_session_finished_switches_back_to_greeter_tty(monkeypatch):
    state = wldm.daemon.DaemonState("/srv/wldm/wldm.sh", 3)
    state.greeter_writer = DummyWriter()
    state.console = 77
    state.greeter_tty = 7
    proc = DummyAsyncProc(pid=333, returncode=0)
    state.active_sessions[333] = wldm.daemon.SessionState(proc=proc, username="alice", command="sway")
    changes = []

    monkeypatch.setattr(wldm.tty, "change", lambda console, tty: changes.append((console, tty)) or True)

    asyncio.run(wldm.daemon.send_session_finished(state, state.active_sessions[333]))

    assert changes == [(77, 7)]
    assert 333 not in state.active_sessions
    events = [wldm.protocol.decode_message(line) for line in state.greeter_writer.lines]
    assert any(event.get("event") == wldm.protocol.EVENT_SESSION_FINISHED for event in events)
    finished = next(event for event in events if event.get("event") == wldm.protocol.EVENT_SESSION_FINISHED)
    assert finished["payload"]["failed"] is False
    assert finished["payload"]["message"] == "Session finished."


def test_send_session_finished_saves_last_successful_session(tmp_path, monkeypatch):
    state = wldm.daemon.DaemonState("/srv/wldm/wldm.sh", 3, state_dir=str(tmp_path))
    state.greeter_writer = DummyWriter()
    proc = DummyAsyncProc(pid=555, returncode=0)
    session = wldm.daemon.SessionState(proc=proc, username="alice", command="sway --debug")
    state.active_sessions[555] = session

    monkeypatch.setattr(wldm.tty, "change", lambda console, tty: True)

    asyncio.run(wldm.daemon.send_session_finished(state, session))

    assert state.last_username == "alice"
    assert state.last_session_command == "sway --debug"
    assert (tmp_path / wldm.state.LAST_SESSION_FILE).read_text(encoding="utf-8") == (
        "[session]\nusername = alice\ncommand = sway --debug\n"
    )


def test_send_session_finished_reports_failed_session(monkeypatch):
    state = wldm.daemon.DaemonState("/srv/wldm/wldm.sh", 3)
    state.greeter_writer = DummyWriter()
    proc = DummyAsyncProc(pid=444, returncode=7)
    state.active_sessions[444] = wldm.daemon.SessionState(proc=proc, username="alice", command="sway")

    monkeypatch.setattr(wldm.tty, "change", lambda console, tty: True)

    asyncio.run(wldm.daemon.send_session_finished(state, state.active_sessions[444]))

    event = next(wldm.protocol.decode_message(line) for line in state.greeter_writer.lines)
    assert event["payload"]["failed"] is True
    assert event["payload"]["message"] == "Session failed with exit status 7."


def test_handle_greeter_client_marks_greeter_ready(monkeypatch):
    state = wldm.daemon.DaemonState("/srv/wldm/wldm.sh", 3, greeter_uid=32)
    writer = DummyWriter()
    req = wldm.protocol.new_request(wldm.protocol.ACTION_REBOOT, {})
    encoded = wldm.protocol.encode_message(req)
    reader = DummyReader([encoded[:4], encoded[4:], b""])
    calls = []

    async def fake_handle_request_async(state_arg, req_arg, cfg_arg):
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
    state = wldm.daemon.DaemonState(["/usr/bin/python3", "/srv/wldm/src/wldm/command.py"], 3, seat="seat9")
    state.last_username = "alice"
    state.last_session_command = "labwc"
    cfg = make_config(command="labwc --", greeter_log="/tmp/custom-greeter.log", user_sessions="no", theme="retro")
    cfg["daemon"]["suspend-command"] = "do-suspend"
    cfg.sections["keyboard"] = {
        "rules": "evdev",
        "model": "pc105",
        "layout": "us,ru",
        "variant": "",
        "options": "grp:alt_shift_toggle",
    }
    calls = {}
    proc = DummyAsyncProc(pid=4321, returncode=0)

    async def fake_create_subprocess_exec(*cmd, env=None, start_new_session=False):
        calls["cmd"] = cmd
        calls["env"] = env
        return proc

    monkeypatch.setattr(wldm.daemon.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    result = asyncio.run(wldm.daemon.start_greeter(state, cfg, 7, "/tmp/wldm/greeter.sock"))

    assert result is proc
    assert calls["cmd"][:9] == (
        "/usr/bin/python3",
        "/srv/wldm/src/wldm/command.py",
        "greeter-session",
        "--tty",
        "7",
        "--pam-service",
        "system-login",
        "gdm",
        "gdm",
    )
    assert calls["cmd"][9:] == (
        "labwc",
        "--",
        "/usr/bin/python3",
        "/srv/wldm/src/wldm/command.py",
        "greeter",
    )
    assert calls["env"]["WLDM_SOCKET"] == "/tmp/wldm/greeter.sock"
    assert calls["env"]["WLDM_SEAT"] == "seat9"
    assert calls["env"]["WLDM_THEME"] == "retro"
    assert calls["env"]["WLDM_GREETER_SESSION_DIRS"] == "/usr/share/wayland-sessions"
    assert calls["env"]["WLDM_GREETER_USER_SESSION_DIR"] == ".local/share/wayland-sessions"
    assert calls["env"]["WLDM_ACTIONS"] == "poweroff:reboot:suspend"
    assert calls["env"]["WLDM_GREETER_STDERR_LOG"] == "/tmp/custom-greeter.log"
    assert calls["env"]["WLDM_GREETER_USER_SESSIONS"] == "no"
    assert calls["env"]["WLDM_LAST_USERNAME"] == "alice"
    assert calls["env"]["WLDM_LAST_SESSION_COMMAND"] == "labwc"
    assert calls["env"]["XKB_DEFAULT_RULES"] == "evdev"
    assert calls["env"]["XKB_DEFAULT_MODEL"] == "pc105"
    assert calls["env"]["XKB_DEFAULT_LAYOUT"] == "us,ru"
    assert calls["env"]["XKB_DEFAULT_OPTIONS"] == "grp:alt_shift_toggle"


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
    state.active_sessions = {22: wldm.daemon.SessionState(proc=session, username="alice", command="sway")}
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
    monkeypatch.setattr(wldm.tty, "open_console", lambda: None)

    result = asyncio.run(wldm.daemon.run_daemon_async(SimpleNamespace(tty=None), make_config()))

    assert result == wldm.daemon.wldm.EX_FAILURE


def test_run_daemon_async_fails_when_tty_switch_fails(monkeypatch):
    closed = []

    monkeypatch.setattr(wldm.tty, "open_console", lambda: 88)
    monkeypatch.setattr(wldm.tty, "change", lambda console, tty: False)
    monkeypatch.setattr(wldm.daemon.os, "close", lambda fd: closed.append(fd))

    result = asyncio.run(wldm.daemon.run_daemon_async(SimpleNamespace(tty=None), make_config(tty="7")))

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

    monkeypatch.setattr(wldm.tty, "open_console", lambda: 88)
    monkeypatch.setattr(wldm.tty, "change", lambda console, tty: True)
    monkeypatch.setattr(wldm.daemon.pwd, "getpwnam", lambda user: SimpleNamespace(pw_uid=32))
    monkeypatch.setattr(wldm.daemon, "create_greeter_listener", lambda user, group, path: listener)
    monkeypatch.setattr(wldm.daemon.asyncio, "start_unix_server", fake_start_unix_server)
    monkeypatch.setattr(wldm.daemon, "start_greeter", fake_start_greeter)
    monkeypatch.setattr(wldm.daemon, "cleanup_async", fake_cleanup_async)
    monkeypatch.setattr(wldm.daemon.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(wldm.daemon.os, "close", lambda fd: None)

    result = asyncio.run(
        wldm.daemon.run_daemon_async(SimpleNamespace(tty=None), make_config(max_restarts="2"))
    )

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

    monkeypatch.setattr(wldm.tty, "open_console", lambda: 88)
    monkeypatch.setattr(wldm.tty, "change", lambda console, tty: True)
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

    result = asyncio.run(wldm.daemon.run_daemon_async(SimpleNamespace(tty=None), make_config()))

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

    monkeypatch.setattr(wldm.tty, "open_console", lambda: 88)
    monkeypatch.setattr(wldm.tty, "change", lambda console, tty: True)
    monkeypatch.setattr(wldm.daemon.pwd, "getpwnam", lambda user: SimpleNamespace(pw_uid=32))
    monkeypatch.setattr(wldm.daemon, "create_greeter_listener", lambda user, group, path: listener)
    monkeypatch.setattr(wldm.daemon.asyncio, "start_unix_server", fake_start_unix_server)
    monkeypatch.setattr(wldm.daemon, "start_greeter", fake_start_greeter)
    monkeypatch.setattr(wldm.daemon, "cleanup_async", fake_cleanup_async)
    monkeypatch.setattr(wldm.daemon.os, "close", lambda fd: closed.append(fd))

    try:
        asyncio.run(wldm.daemon.run_daemon_async(SimpleNamespace(tty=None), make_config()))
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

    monkeypatch.setattr(wldm.config, "read_config", lambda: cfg)
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
