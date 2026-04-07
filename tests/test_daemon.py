# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026  Alexey Gladkov <legion@kernel.org>

import asyncio
import signal
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
    def __init__(self, peer_pid=200, peer_uid=32, peer_gid=32):
        self.lines = []
        self.closed = False
        self.waited = False
        self.peer_pid = peer_pid
        self.peer_uid = peer_uid
        self.peer_gid = peer_gid

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
                return (
                    writer.peer_pid.to_bytes(4, "little")
                    + writer.peer_uid.to_bytes(4, "little")
                    + writer.peer_gid.to_bytes(4, "little")
                )

        return DummySocket()


class DummyAsyncProc:
    def __init__(self, pid=1234, returncode=0):
        self.pid = pid
        self.returncode = returncode
        self.wait_calls = 0

    async def wait(self):
        self.wait_calls += 1
        return self.returncode


def make_config(user="gdm",
                group="gdm",
                tty="7",
                theme="default",
                session_dirs="/usr/share/wayland-sessions",
                user_session_dir=".local/share/wayland-sessions",
                greeter_state_dir="",
                command="cage -s -m last --",
                pam_service="system-login",
                max_restarts="3",
                user_sessions="yes",
                seat="seat0",
                daemon_log="/tmp/wldm/daemon.log",
                greeter_log="/tmp/wldm/greeter.log"):
    return wldm.inifile.IniFile({
        "daemon": {
            "seat": seat,
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
            "state-dir": greeter_state_dir,
            "command": command,
            "pam-service": pam_service,
            "max-restarts": max_restarts,
            "user-sessions": user_sessions,
            "log-path": greeter_log,
        },
        "dbus": {
            "enabled": "no",
            "user": user,
            "service": "org.freedesktop.DisplayManager",
            "log-path": "",
        },
    })
def test_process_request_accepts_poweroff_and_reboot():
    cfg = make_config()
    cfg["daemon"]["poweroff-command"] = "do-poweroff"
    cfg["daemon"]["reboot-command"] = "do-reboot"
    cfg["daemon"]["suspend-command"] = "do-suspend"
    cfg["daemon"]["hibernate-command"] = "do-hibernate"

    state = wldm.daemon.DaemonState("/srv/wldm/wldm.sh", 3)
    poweroff = wldm.daemon.process_request(
        state, "greeter", wldm.protocol.new_request(wldm.protocol.ACTION_POWEROFF, {}), cfg
    )
    reboot = wldm.daemon.process_request(
        state, "greeter", wldm.protocol.new_request(wldm.protocol.ACTION_REBOOT, {}), cfg
    )
    suspend = wldm.daemon.process_request(
        state, "greeter", wldm.protocol.new_request(wldm.protocol.ACTION_SUSPEND, {}), cfg
    )
    hibernate = wldm.daemon.process_request(
        state, "greeter", wldm.protocol.new_request(wldm.protocol.ACTION_HIBERNATE, {}), cfg
    )

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
    state = wldm.daemon.DaemonState("/srv/wldm/wldm.sh", 3)

    outcome = wldm.daemon.process_request(
        state, "greeter", wldm.protocol.new_request(wldm.protocol.ACTION_SUSPEND, {}), cfg
    )

    assert outcome.response["error"]["code"] == "action_disabled"


def test_process_request_replies_with_bad_request_for_unknown_payload():
    outcome = wldm.daemon.process_request(
        wldm.daemon.DaemonState("/srv/wldm/wldm.sh", 3), "greeter", {}, make_config()
    )

    assert outcome.response == {
        "v": 1,
        "id": "",
        "type": "response",
        "action": "",
        "ok": False,
        "error": {"code": "bad_request", "message": "Malformed request"},
    }
    assert outcome.event is None


def test_process_request_rejects_overlong_username():
    state = wldm.daemon.DaemonState("/srv/wldm/wldm.sh", 3)
    req = wldm.protocol.new_request(
        wldm.protocol.ACTION_CREATE_SESSION,
        {
            "username": wldm.secret.SecretBytes(b"a" * 257),
        },
    )

    outcome = wldm.daemon.process_request(state, "greeter", req, make_config())

    assert outcome.response["error"] == {"code": "bad_request", "message": "Username is too long"}
def test_process_request_replies_with_unknown_action_error():
    req = wldm.protocol.new_request("mystery", {})
    state = wldm.daemon.DaemonState("/srv/wldm/wldm.sh", 3)

    outcome = wldm.daemon.process_request(state, "greeter", req, make_config())

    assert outcome.response["error"]["code"] == "unknown_action"


def test_process_request_create_session_returns_secret_prompt():
    state = wldm.daemon.DaemonState("/srv/wldm/wldm.sh", 3)
    req = wldm.protocol.new_request(
        wldm.protocol.ACTION_CREATE_SESSION,
        {"username": wldm.secret.SecretBytes(b"alice")},
    )

    outcome = wldm.daemon.process_request(state, "greeter", req, make_config())

    assert outcome.response["payload"] == {
        "state": "pending",
        "message": {"style": "secret", "text": "Password:"},
    }
    assert state.clients["greeter"].auth_session == wldm.daemon.AuthSessionState(username="alice", verified=False)
    assert req["payload"]["username"].as_bytes() == b""


def test_process_request_continue_session_requires_configured_session():
    state = wldm.daemon.DaemonState("/srv/wldm/wldm.sh", 3)
    req = wldm.protocol.new_request(
        wldm.protocol.ACTION_CONTINUE_SESSION,
        {"response": wldm.secret.SecretBytes(b"secret")},
    )

    outcome = wldm.daemon.process_request(state, "greeter", req, make_config())

    assert outcome.response["error"] == {
        "code": "session_not_found",
        "message": "No session is being configured",
    }


def test_process_request_continue_session_marks_session_ready(monkeypatch):
    state = wldm.daemon.DaemonState("/srv/wldm/wldm.sh", 3)
    state.clients["greeter"].auth_session = wldm.daemon.AuthSessionState(username="alice", verified=False)
    req = wldm.protocol.new_request(
        wldm.protocol.ACTION_CONTINUE_SESSION,
        {"response": wldm.secret.SecretBytes(b"secret")},
    )

    monkeypatch.setattr(wldm.daemon, "verify_creds", lambda username, password: True)

    outcome = wldm.daemon.process_request(state, "greeter", req, make_config())

    assert outcome.response["payload"] == {"state": "ready"}
    assert state.clients["greeter"].auth_session == wldm.daemon.AuthSessionState(username="alice", verified=True)
    assert req["payload"]["response"].as_bytes() == b""


def test_process_request_start_session_requires_ready_state():
    state = wldm.daemon.DaemonState("/srv/wldm/wldm.sh", 3)
    state.clients["greeter"].auth_session = wldm.daemon.AuthSessionState(username="alice", verified=False)
    req = wldm.protocol.new_request(
        wldm.protocol.ACTION_START_SESSION,
        {"command": "sway", "desktop_names": ["sway"]},
    )

    outcome = wldm.daemon.process_request(state, "greeter", req, make_config())

    assert outcome.response["error"] == {
        "code": "session_not_ready",
        "message": "Session is not ready",
    }


def test_process_request_start_session_after_ready():
    state = wldm.daemon.DaemonState("/srv/wldm/wldm.sh", 3)
    state.clients["greeter"].auth_session = wldm.daemon.AuthSessionState(username="alice", verified=True)
    req = wldm.protocol.new_request(
        wldm.protocol.ACTION_START_SESSION,
        {"command": "startplasma-wayland --debug", "desktop_names": ["plasma", "kde"]},
    )

    outcome = wldm.daemon.process_request(state, "greeter", req, make_config())

    assert outcome.response["ok"] is True
    assert outcome.event == {
        "v": 1,
        "type": "event",
        "event": wldm.protocol.EVENT_SESSION_STARTING,
        "payload": {"command": "startplasma-wayland --debug", "desktop_names": ["plasma", "kde"]},
    }
    assert outcome.session_username == "alice"
    assert outcome.session_command == "startplasma-wayland --debug"
    assert outcome.session_desktop_names == ["plasma", "kde"]
    assert state.clients["greeter"].auth_session is None


def test_process_request_cancel_session_clears_auth_state():
    state = wldm.daemon.DaemonState("/srv/wldm/wldm.sh", 3)
    state.clients["greeter"].auth_session = wldm.daemon.AuthSessionState(username="alice", verified=False)
    req = wldm.protocol.new_request(wldm.protocol.ACTION_CANCEL_SESSION, {})

    outcome = wldm.daemon.process_request(state, "greeter", req, make_config())

    assert outcome.response["ok"] is True
    assert state.clients["greeter"].auth_session is None


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


def test_control_command_uses_configured_system_commands():
    cfg = make_config()
    cfg["daemon"]["poweroff-command"] = "do-poweroff --now"
    cfg["daemon"]["reboot-command"] = "do-reboot --cold"
    cfg["daemon"]["suspend-command"] = "do-suspend"
    cfg["daemon"]["hibernate-command"] = "do-hibernate --deep"

    assert wldm.daemon.control_command(cfg, wldm.protocol.ACTION_POWEROFF) == "do-poweroff --now"
    assert wldm.daemon.control_command(cfg, wldm.protocol.ACTION_REBOOT) == "do-reboot --cold"
    assert wldm.daemon.control_command(cfg, wldm.protocol.ACTION_SUSPEND) == "do-suspend"
    assert wldm.daemon.control_command(cfg, wldm.protocol.ACTION_HIBERNATE) == "do-hibernate --deep"


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


def test_process_request_returns_state_snapshot():
    state = wldm.daemon.DaemonState("/srv/wldm/wldm.sh", 3, seat="seat9")
    state.clients["greeter"].ready = True
    state.active_sessions[42] = wldm.daemon.SessionState(
        proc=DummyAsyncProc(pid=42, returncode=None),
        username="alice",
        command="sway",
    )

    outcome = wldm.daemon.process_request(
        state,
        "greeter",
        wldm.protocol.new_request(wldm.protocol.ACTION_GET_STATE, {}),
        make_config(seat="seat9"),
    )

    assert outcome.response["payload"] == {
        "seat": "seat9",
        "greeter_ready": True,
        "active_sessions": [{"pid": 42, "username": "alice", "command": "sway"}],
    }


def test_handle_request_async_starts_session_after_auth(monkeypatch):
    state = wldm.daemon.DaemonState(["/usr/bin/python3", "/srv/wldm/src/wldm/command.py"], 3)
    state.clients["greeter"].writer = DummyWriter()
    state.clients["greeter"].auth_session = wldm.daemon.AuthSessionState(username="alice", verified=True)
    req = wldm.protocol.new_request(
        wldm.protocol.ACTION_START_SESSION,
        {
            "command": "startplasma-wayland --debug",
            "desktop_names": ["plasma", "kde"],
        },
    )
    proc = DummyAsyncProc(pid=777, returncode=0)
    task_calls = []

    async def fake_create_subprocess_exec(*cmd, env=None):
        assert cmd == (
            "/usr/bin/python3",
            "/srv/wldm/src/wldm/command.py",
            "user-session",
            "--",
            "alice",
        )
        assert env["WLDM_SEAT"] == "seat0"
        assert env["WLDM_SESSION_COMMAND"] == "startplasma-wayland --debug"
        assert env["WLDM_SESSION_DESKTOP_NAMES"] == "plasma:kde"
        return proc

    async def fake_exec(*cmd, env=None, start_new_session=False):
        assert start_new_session is False
        return await fake_create_subprocess_exec(*cmd, env=env)

    monkeypatch.setattr(wldm.daemon.asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr(wldm.daemon, "track_session_task", lambda state, task: task_calls.append((state, task)))

    asyncio.run(wldm.daemon.handle_request_async(state, "greeter", req, make_config()))

    assert 777 in state.active_sessions or task_calls
    assert any(
        wldm.protocol.decode_message(line).get("event") == wldm.protocol.EVENT_SESSION_STARTING
        for line in state.clients["greeter"].writer.lines
    )


def test_handle_request_async_runs_control_command(monkeypatch):
    state = wldm.daemon.DaemonState("/srv/wldm/wldm.sh", 3)
    state.clients["greeter"].writer = DummyWriter()
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

    asyncio.run(wldm.daemon.handle_request_async(state, "greeter", req, cfg))

    assert calls["cmd"] == ("/bin/sh", "-c", "do-poweroff --now")
    assert wldm.protocol.decode_message(state.clients["greeter"].writer.lines[0])["payload"] == {"accepted": True}


def test_handle_request_async_returns_get_state_to_requesting_client(monkeypatch):
    state = wldm.daemon.DaemonState("/srv/wldm/wldm.sh", 3)
    state.clients["greeter"].writer = DummyWriter()
    state.clients["adapter"] = wldm.daemon.ClientState(writer=DummyWriter())
    req = wldm.protocol.new_request(wldm.protocol.ACTION_GET_STATE, {})

    asyncio.run(wldm.daemon.handle_request_async(state, "adapter", req, make_config()))

    assert len(state.clients["adapter"].writer.lines) == 1
    assert wldm.protocol.decode_message(state.clients["adapter"].writer.lines[0])["action"] == (
        wldm.protocol.ACTION_GET_STATE
    )


def test_broadcast_state_changed_sends_snapshot_to_all_clients():
    state = wldm.daemon.DaemonState("/srv/wldm/wldm.sh", 3, seat="seat9")
    state.clients["greeter"].writer = DummyWriter()
    state.clients["adapter"] = wldm.daemon.ClientState(writer=DummyWriter())

    asyncio.run(wldm.daemon.broadcast_state_changed(state))

    for name in ["greeter", "adapter"]:
        message = wldm.protocol.decode_message(state.clients[name].writer.lines[0])
        assert message["event"] == wldm.protocol.EVENT_STATE_CHANGED
        assert message["payload"]["seat"] == "seat9"


def test_send_session_finished_switches_back_to_greeter_tty(monkeypatch):
    state = wldm.daemon.DaemonState("/srv/wldm/wldm.sh", 3)
    state.clients["greeter"].writer = DummyWriter()
    state.console = 77
    state.greeter_tty = 7
    proc = DummyAsyncProc(pid=333, returncode=0)
    state.active_sessions[333] = wldm.daemon.SessionState(proc=proc, username="alice", command="sway")
    changes = []

    monkeypatch.setattr(wldm.tty, "change", lambda console, tty: changes.append((console, tty)) or True)

    asyncio.run(wldm.daemon.send_session_finished(state, state.active_sessions[333]))

    assert changes == [(77, 7)]
    assert 333 not in state.active_sessions
    events = [wldm.protocol.decode_message(line) for line in state.clients["greeter"].writer.lines]
    assert any(event.get("event") == wldm.protocol.EVENT_SESSION_FINISHED for event in events)
    finished = next(event for event in events if event.get("event") == wldm.protocol.EVENT_SESSION_FINISHED)
    assert finished["payload"]["failed"] is False
    assert finished["payload"]["message"] == "Session finished."

def test_send_session_finished_reports_failed_session(monkeypatch):
    state = wldm.daemon.DaemonState("/srv/wldm/wldm.sh", 3)
    state.clients["greeter"].writer = DummyWriter()
    proc = DummyAsyncProc(pid=444, returncode=7)
    state.active_sessions[444] = wldm.daemon.SessionState(proc=proc, username="alice", command="sway")

    monkeypatch.setattr(wldm.tty, "change", lambda console, tty: True)

    asyncio.run(wldm.daemon.send_session_finished(state, state.active_sessions[444]))

    event = next(wldm.protocol.decode_message(line) for line in state.clients["greeter"].writer.lines)
    assert event["payload"]["failed"] is True
    assert event["payload"]["message"] == "Session failed with exit status 7."


def test_handle_client_marks_client_ready(monkeypatch):
    state = wldm.daemon.DaemonState("/srv/wldm/wldm.sh", 3)
    writer = DummyWriter()
    req = wldm.protocol.new_request(wldm.protocol.ACTION_REBOOT, {})
    encoded = wldm.protocol.encode_message(req)
    reader = DummyReader([encoded[:4], encoded[4:], b""])
    calls = []

    async def fake_handle_request_async(state_arg, name_arg, req_arg, cfg_arg):
        calls.append((state_arg, name_arg, req_arg, cfg_arg))

    monkeypatch.setattr(wldm.daemon, "handle_request_async", fake_handle_request_async)

    cfg = make_config()
    asyncio.run(wldm.daemon.handle_client(state, "greeter", reader, writer, cfg))

    assert state.clients["greeter"].ready is True
    assert calls[0][1] == "greeter"
    assert calls[0][2]["action"] == wldm.protocol.ACTION_REBOOT
    assert calls[0][3] is cfg
    assert writer.closed is True


def test_start_greeter_passes_socket_env(monkeypatch):
    state = wldm.daemon.DaemonState(["/usr/bin/python3", "/srv/wldm/src/wldm/command.py"], 3, seat="seat9")
    cfg = make_config(
        command="labwc --",
        greeter_log="/tmp/custom-greeter.log",
        user_sessions="no",
        theme="retro",
        greeter_state_dir="/tmp/wldm-state",
    )
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

    async def fake_create_subprocess_exec(*cmd, env=None, start_new_session=False, **kwargs):
        calls["cmd"] = cmd
        calls["env"] = env
        calls["kwargs"] = kwargs
        return proc

    monkeypatch.setattr(wldm.daemon.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    class DummySocket:
        def __init__(self, fileno):
            self._fileno = fileno
            self.closed = False

        def fileno(self):
            return self._fileno

        def close(self):
            self.closed = True

    async def fake_open_connection(sock=None):
        calls["sock"] = sock
        return SimpleNamespace(), DummyWriter()

    async def fake_handle_client(state, name, reader, writer, cfg):
        return None

    monkeypatch.setattr(wldm.daemon, "create_client_socketpair", lambda: (DummySocket(10), DummySocket(11)))
    monkeypatch.setattr(wldm.daemon.asyncio, "open_connection", fake_open_connection)
    monkeypatch.setattr(wldm.daemon, "handle_client", fake_handle_client)

    result = asyncio.run(wldm.daemon.start_greeter(state, cfg, 7))

    assert result is proc
    assert state.clients["greeter"].task is not None
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
    assert calls["env"]["WLDM_SOCKET_FD"] == "11"
    assert calls["env"]["WLDM_SEAT"] == "seat9"
    assert calls["env"]["WLDM_THEME"] == "retro"
    assert calls["env"]["WLDM_GREETER_COMMAND"] == "labwc --"
    assert calls["env"]["WLDM_GREETER_SESSION_DIRS"] == "/usr/share/wayland-sessions"
    assert calls["env"]["WLDM_GREETER_USER_SESSION_DIR"] == ".local/share/wayland-sessions"
    assert calls["env"]["WLDM_ACTIONS"] == "poweroff:reboot:suspend"
    assert calls["env"]["WLDM_GREETER_STDERR_LOG"] == "/tmp/custom-greeter.log"
    assert calls["env"]["WLDM_GREETER_USER_SESSIONS"] == "no"
    assert calls["env"]["WLDM_STATE_FILE"] == "/tmp/wldm-state/last-session"
    assert calls["env"]["XKB_DEFAULT_RULES"] == "evdev"
    assert calls["env"]["XKB_DEFAULT_MODEL"] == "pc105"
    assert calls["env"]["XKB_DEFAULT_LAYOUT"] == "us,ru"
    assert calls["env"]["XKB_DEFAULT_OPTIONS"] == "grp:alt_shift_toggle"
    assert calls["sock"].fileno() == 10
    assert calls["kwargs"]["pass_fds"] == (11,)


def test_start_dbus_adapter_is_optional():
    state = wldm.daemon.DaemonState("/srv/wldm/wldm.sh", 3)

    result = asyncio.run(wldm.daemon.start_dbus_adapter(state, make_config()))

    assert result is None


def test_start_dbus_adapter_starts_internal_client(monkeypatch):
    state = wldm.daemon.DaemonState(["/usr/bin/python3", "/srv/wldm/src/wldm/command.py"], 3)
    cfg = make_config(user="adapter-user")
    cfg["dbus"]["enabled"] = "yes"
    cfg["dbus"]["user"] = "adapter-user"
    cfg["dbus"]["service"] = "org.example.DisplayManager"
    cfg["dbus"]["log-path"] = "/tmp/wldm/dbus.log"
    proc = DummyAsyncProc(pid=5555, returncode=0)
    calls = {}

    async def fake_start_client(state_arg, name_arg, cfg_arg, argv_arg, env_arg):
        calls["state"] = state_arg
        calls["name"] = name_arg
        calls["cfg"] = cfg_arg
        calls["argv"] = argv_arg
        calls["env"] = env_arg
        return proc

    monkeypatch.setattr(wldm.daemon, "start_client", fake_start_client)

    result = asyncio.run(wldm.daemon.start_dbus_adapter(state, cfg))

    assert result is proc
    assert calls["state"] is state
    assert calls["name"] == "dbus-adapter"
    assert calls["cfg"] is cfg
    assert calls["argv"] == [
        "/usr/bin/python3",
        "/srv/wldm/src/wldm/command.py",
        "dbus-adapter",
        "adapter-user",
        "org.example.DisplayManager",
    ]
    assert isinstance(calls["env"], dict)
    assert calls["env"]["WLDM_DBUS_LOG_PATH"] == "/tmp/wldm/dbus.log"


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
    state.clients["greeter"].proc = greeter
    state.active_sessions = {22: wldm.daemon.SessionState(proc=session, username="alice", command="sway")}
    calls = []

    async def fake_terminate(proc, name, timeout=5.0):
        calls.append((proc.pid, name, timeout))

    async def fake_close_channel(state):
        return None

    monkeypatch.setattr(wldm.daemon, "terminate_process_tree", fake_terminate)
    monkeypatch.setattr(wldm.daemon, "close_greeter_channel", fake_close_channel)

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
    greeters = [DummyAsyncProc(pid=1, returncode=5), DummyAsyncProc(pid=2, returncode=6)]
    sleeps = []

    async def fake_start_greeter(state, cfg, greeter_tty):
        return greeters.pop(0)

    async def fake_cleanup_async(state):
        return None

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr(wldm.tty, "open_console", lambda: 88)
    monkeypatch.setattr(wldm.tty, "change", lambda console, tty: True)
    monkeypatch.setattr(wldm.daemon, "start_greeter", fake_start_greeter)
    monkeypatch.setattr(wldm.daemon, "cleanup_async", fake_cleanup_async)
    monkeypatch.setattr(wldm.daemon.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(wldm.daemon.os, "close", lambda fd: None)

    result = asyncio.run(
        wldm.daemon.run_daemon_async(SimpleNamespace(tty=None), make_config(max_restarts="2"))
    )

    assert result == wldm.daemon.wldm.EX_FAILURE
    assert sleeps == [1]


def test_run_daemon_async_restarts_dbus_adapter_without_stopping(monkeypatch):
    waits = iter([(False, "dbus-adapter"), (True, "")])
    starts = {"greeter": 0, "dbus-adapter": 0}
    stop_event = asyncio.Event()

    async def fake_start_greeter(state, cfg, greeter_tty):
        starts["greeter"] += 1
        proc = DummyAsyncProc(pid=100 + starts["greeter"], returncode=None)
        state.clients["greeter"].proc = proc
        state.clients["greeter"].ready = True
        return proc

    async def fake_start_dbus_adapter(state, cfg):
        starts["dbus-adapter"] += 1
        proc = DummyAsyncProc(pid=200 + starts["dbus-adapter"], returncode=1)
        state.clients.setdefault("dbus-adapter", wldm.daemon.ClientState()).proc = proc
        return proc

    async def fake_wait_for_stop_or_client(state, client_names, event):
        return next(waits)

    async def fake_close_client_channel(state, name):
        return None

    async def fake_cleanup_async(state):
        return None

    monkeypatch.setattr(wldm.tty, "open_console", lambda: 88)
    monkeypatch.setattr(wldm.tty, "change", lambda console, tty: True)
    monkeypatch.setattr(wldm.daemon, "start_greeter", fake_start_greeter)
    monkeypatch.setattr(wldm.daemon, "start_dbus_adapter", fake_start_dbus_adapter)
    monkeypatch.setattr(wldm.daemon, "wait_for_stop_or_client", fake_wait_for_stop_or_client)
    monkeypatch.setattr(wldm.daemon, "close_client_channel", fake_close_client_channel)
    monkeypatch.setattr(wldm.daemon, "cleanup_async", fake_cleanup_async)
    monkeypatch.setattr(wldm.daemon, "install_stop_handlers", lambda loop, event: stop_event.set() if event is stop_event else None)
    monkeypatch.setattr(wldm.daemon, "remove_stop_handlers", lambda loop: None)
    monkeypatch.setattr(wldm.daemon.os, "close", lambda fd: None)
    monkeypatch.setattr(wldm.daemon.asyncio, "Event", lambda: stop_event)

    cfg = make_config()
    cfg["dbus"]["enabled"] = "yes"

    result = asyncio.run(wldm.daemon.run_daemon_async(SimpleNamespace(tty=None), cfg))

    assert result == wldm.daemon.wldm.EX_SUCCESS
    assert starts["greeter"] == 1
    assert starts["dbus-adapter"] == 2


def test_run_daemon_async_cleans_up_after_stop_signal(monkeypatch):
    cleanup_calls = []
    closed = []
    stop_event = asyncio.Event()

    async def fake_start_greeter(state, cfg, greeter_tty):
        return DummyAsyncProc(pid=1, returncode=None)

    async def fake_wait_for_stop_or_process(proc, event):
        assert event is stop_event
        stop_event.set()
        return True

    async def fake_cleanup_async(state):
        cleanup_calls.append(state)

    monkeypatch.setattr(wldm.tty, "open_console", lambda: 88)
    monkeypatch.setattr(wldm.tty, "change", lambda console, tty: True)
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
    assert closed == [88]


def test_run_daemon_async_cleans_up_on_cancellation(monkeypatch):
    cleanup_calls = []
    closed = []

    async def fake_start_greeter(state, cfg, greeter_tty):
        raise asyncio.CancelledError()

    async def fake_cleanup_async(state):
        cleanup_calls.append(state)

    monkeypatch.setattr(wldm.tty, "open_console", lambda: 88)
    monkeypatch.setattr(wldm.tty, "change", lambda console, tty: True)
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
