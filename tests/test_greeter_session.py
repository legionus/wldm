# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026  Alexey Gladkov <legion@kernel.org>

from types import SimpleNamespace
import pwd

import wldm.greeter_session
import wldm.pam
import wldm.tty


def test_new_greeter_environ_preserves_safe_base_env_and_adds_runtime_dir(monkeypatch):
    monkeypatch.setattr(
        wldm.greeter_session.os,
        "environ",
        {
            "PATH": "/usr/bin",
            "PYTHONPATH": "/srv/wldm/src",
            "XKB_DEFAULT_LAYOUT": "us,ru",
            "XKB_DEFAULT_OPTIONS": "grp:alt_shift_toggle",
            "XDG_SESSION_ID": "19",
            "XDG_RUNTIME_DIR": "/run/user/0",
        },
    )
    monkeypatch.setattr(
        wldm.pam,
        "getenvlist",
        lambda pamh: {"XDG_RUNTIME_DIR": "/run/user/1001", "LANG": "C.UTF-8"},
    )
    pw = pwd.struct_passwd(("gdm", "x", 1001, 1001, "", "/var/lib/gdm", "/bin/false"))

    env = wldm.greeter_session.new_greeter_environ(object(), pw)

    assert env["PATH"] == "/usr/bin"
    assert env["PYTHONPATH"] == "/srv/wldm/src"
    assert env["XKB_DEFAULT_LAYOUT"] == "us,ru"
    assert env["XKB_DEFAULT_OPTIONS"] == "grp:alt_shift_toggle"
    assert env["HOME"] == "/var/lib/gdm"
    assert env["USER"] == "gdm"
    assert env["XDG_RUNTIME_DIR"] == "/run/user/1001"
    assert env["LANG"] == "C.UTF-8"
    assert "XDG_SESSION_ID" not in env


def test_new_greeter_environ_falls_back_to_user_runtime_dir(monkeypatch):
    monkeypatch.setattr(wldm.greeter_session.os, "environ", {"PATH": "/usr/bin"})
    monkeypatch.setattr(wldm.pam, "getenvlist", lambda pamh: {})
    pw = pwd.struct_passwd(("gdm", "x", 32, 32, "", "/var/lib/gdm", "/bin/false"))

    env = wldm.greeter_session.new_greeter_environ(object(), pw)

    assert env["XDG_RUNTIME_DIR"] == "/run/user/32"


def test_cmd_main_runs_greeter_session(monkeypatch):
    pw = pwd.struct_passwd(("gdm", "x", 1001, 1001, "", "/var/lib/gdm", "/bin/false"))
    calls = {}
    ttydev = SimpleNamespace(fd=55, filename="/dev/tty7", number=7)

    class DummyContext:
        def __init__(self, value):
            self.value = value

        def __enter__(self):
            return self.value

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(wldm.greeter_session.pwd, "getpwnam", lambda username: pw)
    monkeypatch.setattr(wldm.greeter_session.grp, "getgrnam", lambda group: SimpleNamespace(gr_gid=1001))
    monkeypatch.setattr(wldm.greeter_session.os, "access", lambda path, mode: True)
    monkeypatch.setattr(wldm.greeter_session, "redirect_greeter_stderr", lambda: calls.update({"redirected": True}))
    monkeypatch.setattr(wldm.greeter_session, "open_console_fd", lambda: DummyContext(88))
    monkeypatch.setattr(wldm.greeter_session.wldm.tty, "TTYdevice", lambda console, uid, number=0: ttydev)
    monkeypatch.setattr(wldm.greeter_session, "prepare_greeter_terminal", lambda tty: calls.update({"tty": tty.number}))
    monkeypatch.setattr(
        wldm.greeter_session,
        "open_greeter_pam_session", lambda pam_service, pw_arg, tty: DummyContext("pamh"),
    )
    monkeypatch.setattr(wldm.greeter_session, "new_greeter_environ", lambda pamh, pw_arg: {"HOME": pw_arg.pw_dir})
    monkeypatch.setattr(
        wldm.greeter_session,
        "exec_greeter_program",
        lambda username, uid, gid, workdir, prog_args, env:
            calls.update({
                "username": username,
                "uid": uid,
                "gid": gid,
                "workdir": workdir,
                "prog_args": prog_args,
                "env": env,
            }),
    )

    result = wldm.greeter_session.cmd_main(
        SimpleNamespace(
            username="gdm",
            group="gdm",
            tty=7,
            pam_service="system-login",
            prog="cage",
            args=["-s", "-m", "last"],
        )
    )

    assert result == wldm.greeter_session.wldm.EX_SUCCESS
    assert calls["redirected"] is True
    assert calls["username"] == "gdm"
    assert calls["uid"] == 1001
    assert calls["gid"] == 1001
    assert calls["tty"] == 7
    assert calls["workdir"] == "/var/lib/gdm"
    assert calls["prog_args"] == ["cage", "-s", "-m", "last"]
    assert calls["env"] == {"HOME": "/var/lib/gdm"}


def test_prepare_greeter_terminal_switches_and_sets_controlling_tty(monkeypatch):
    calls = []

    class DummyTTY:
        fd = 55
        filename = "/dev/tty7"

        def switch(self):
            calls.append(("tty_switch",))

    monkeypatch.setattr(wldm.greeter_session.os, "setsid", lambda: calls.append(("setsid",)))
    monkeypatch.setattr(
        wldm.tty,
        "make_control_tty",
        lambda fd: calls.append(("make_control_tty", fd)) or True,
    )

    wldm.greeter_session.prepare_greeter_terminal(DummyTTY())

    assert calls == [
        ("tty_switch",),
        ("setsid",),
        ("make_control_tty", 55),
    ]


def test_prepare_greeter_terminal_fails_when_tty_cannot_become_controlling(monkeypatch):
    class DummyTTY:
        fd = 55
        filename = "/dev/tty7"

        def switch(self):
            return None

    monkeypatch.setattr(wldm.greeter_session.os, "setsid", lambda: None)
    monkeypatch.setattr(wldm.tty, "make_control_tty", lambda fd: False)

    try:
        wldm.greeter_session.prepare_greeter_terminal(DummyTTY())
    except RuntimeError as exc:
        assert "/dev/tty7" in str(exc)
    else:
        raise AssertionError("prepare_greeter_terminal() should have failed")


def test_redirect_greeter_stderr_replaces_fd_2(monkeypatch):
    calls = []

    class DummyLogFile:
        def fileno(self):
            calls.append(("fileno",))
            return 9

        def close(self):
            calls.append(("close_file",))

    monkeypatch.setattr(
        wldm.greeter_session.wldm,
        "open_secure_append_file",
        lambda path, mode=0o600: calls.append(("open_secure", path, mode)) or DummyLogFile(),
    )
    monkeypatch.setattr(
        wldm.greeter_session.os,
        "dup2",
        lambda src, dst: calls.append(("dup2", src, dst)),
    )

    monkeypatch.delenv("WLDM_GREETER_STDERR_LOG", raising=False)

    wldm.greeter_session.redirect_greeter_stderr()

    assert calls == [
        ("open_secure", "/tmp/wldm/greeter.log", 0o600),
        ("fileno",),
        ("dup2", 9, 2),
        ("close_file",),
    ]


def test_greeter_ipc_fd_marks_inherited_fd_inheritable(monkeypatch):
    calls = []
    monkeypatch.setenv("WLDM_SOCKET_FD", "13")
    monkeypatch.setattr(wldm.greeter_session.os, "set_inheritable", lambda fd, value: calls.append((fd, value)))

    assert wldm.greeter_session.greeter_ipc_fd() == 13
    assert calls == [(13, True)]


def test_exec_greeter_program_preserves_passed_socket_fd(monkeypatch):
    calls = {}

    class DummyLogFile:
        def fileno(self):
            return 9

        def close(self):
            return None

    monkeypatch.setattr(wldm.greeter_session.wldm, "open_secure_append_file", lambda path, mode=0o600: DummyLogFile())
    monkeypatch.setattr(wldm.greeter_session, "log_exec_environment", lambda env, uid, gid, fd=2: None)
    monkeypatch.setattr(wldm.greeter_session.os, "dup2", lambda src, dst: calls.setdefault("dup2", []).append((src, dst)))
    monkeypatch.setattr(wldm.greeter_session, "greeter_ipc_fd", lambda: 13)
    monkeypatch.setattr(
        wldm.greeter_session.wldm,
        "drop_privileges",
        lambda username, uid, gid, workdir: calls.update(
            {"drop_privileges": (username, uid, gid, workdir)}
        ),
    )
    monkeypatch.setattr(
        wldm.greeter_session.wldm,
        "close_inherited_fds",
        lambda keep_fds=(): calls.update({"keep_fds": keep_fds}),
    )
    monkeypatch.setattr(
        wldm.greeter_session.os,
        "execve",
        lambda prog, argv, env: calls.update({"execve": (prog, argv, dict(env))}),
    )
    monkeypatch.setattr(wldm.greeter_session.os, "close", lambda fd: calls.update({"closed_fd": fd}))

    wldm.greeter_session.exec_greeter_program(
        "gdm", 32, 32, "/var/lib/gdm",
        ["cage", "--", "greeter"],
        {"PATH": "/usr/bin"},
    )

    assert calls["dup2"] == [(9, 2)]
    assert calls["drop_privileges"] == ("gdm", 32, 32, "/var/lib/gdm")
    assert calls["keep_fds"] == (13,)
    assert calls["execve"] == (
        "cage",
        ["cage", "--", "greeter"],
        {"PATH": "/usr/bin", "WLDM_SOCKET_FD": "13"},
    )
    assert calls["closed_fd"] == 13
