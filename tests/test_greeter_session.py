# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026  Alexey Gladkov <legion@kernel.org>

from types import SimpleNamespace
import pwd

import wldm.greeter_session


def test_new_greeter_environ_preserves_safe_base_env_and_adds_runtime_dir(monkeypatch):
    monkeypatch.setattr(
        wldm.greeter_session.os,
        "environ",
        {
            "PATH": "/usr/bin",
            "WLDM_PROGNAME": "/srv/wldm/wldm.sh",
            "XDG_SESSION_ID": "19",
            "XDG_RUNTIME_DIR": "/run/user/0",
        },
    )
    monkeypatch.setattr(
        wldm.greeter_session.wldm.pam,
        "getenvlist",
        lambda pamh: {"XDG_RUNTIME_DIR": "/run/user/1001", "LANG": "C.UTF-8"},
    )
    pw = pwd.struct_passwd(("gdm", "x", 1001, 1001, "", "/var/lib/gdm", "/bin/false"))

    env = wldm.greeter_session.new_greeter_environ(object(), pw)

    assert env["PATH"] == "/usr/bin"
    assert env["WLDM_PROGNAME"] == "/srv/wldm/wldm.sh"
    assert env["HOME"] == "/var/lib/gdm"
    assert env["USER"] == "gdm"
    assert env["XDG_RUNTIME_DIR"] == "/run/user/1001"
    assert env["LANG"] == "C.UTF-8"
    assert "XDG_SESSION_ID" not in env


def test_new_greeter_environ_falls_back_to_user_runtime_dir(monkeypatch):
    monkeypatch.setattr(wldm.greeter_session.os, "environ", {"PATH": "/usr/bin"})
    monkeypatch.setattr(wldm.greeter_session.wldm.pam, "getenvlist", lambda pamh: {})
    pw = pwd.struct_passwd(("gdm", "x", 32, 32, "", "/var/lib/gdm", "/bin/false"))

    env = wldm.greeter_session.new_greeter_environ(object(), pw)

    assert env["XDG_RUNTIME_DIR"] == "/run/user/32"


def test_cmd_main_runs_greeter_session(monkeypatch):
    pw = pwd.struct_passwd(("gdm", "x", 1001, 1001, "", "/var/lib/gdm", "/bin/false"))
    calls = {}

    monkeypatch.setattr(wldm.greeter_session.pwd, "getpwnam", lambda username: pw)
    monkeypatch.setattr(wldm.greeter_session.grp, "getgrnam", lambda group: SimpleNamespace(gr_gid=1001))
    monkeypatch.setattr(wldm.greeter_session.os, "access", lambda path, mode: True)
    monkeypatch.setattr(
        wldm.greeter_session,
        "run_greeter_session",
        lambda pw_arg, gid, tty, pam_service, prog, prog_args:
            calls.update({
                "pw": pw_arg,
                "gid": gid,
                "tty": tty,
                "pam_service": pam_service,
                "prog": prog,
                "prog_args": prog_args,
            }) or wldm.greeter_session.wldm.EX_SUCCESS,
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
    assert calls["gid"] == 1001
    assert calls["tty"] == 7
    assert calls["pam_service"] == "system-login"
    assert calls["prog"] == "cage"
    assert calls["prog_args"] == ["cage", "-s", "-m", "last"]


def test_prepare_greeter_terminal_switches_and_sets_controlling_tty(monkeypatch):
    calls = []

    class DummyTTY:
        fd = 55
        filename = "/dev/tty7"

        def switch(self):
            calls.append(("tty_switch",))

    monkeypatch.setattr(wldm.greeter_session.os, "setsid", lambda: calls.append(("setsid",)))
    monkeypatch.setattr(
        wldm.greeter_session.wldm.tty,
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
    monkeypatch.setattr(wldm.greeter_session.wldm.tty, "make_control_tty", lambda fd: False)

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

    wldm.greeter_session.redirect_greeter_stderr()

    assert calls == [
        ("open_secure", "/tmp/wldm/greeter.log", 0o600),
        ("fileno",),
        ("dup2", 9, 2),
        ("close_file",),
    ]


def test_greeter_runtime_helpers_use_environment(monkeypatch):
    monkeypatch.setenv("WLDM_SEAT", "seat9")
    monkeypatch.setenv("WLDM_GREETER_STDERR_LOG", "/tmp/custom-greeter.log")

    assert wldm.greeter_session.greeter_seat() == "seat9"
    assert wldm.greeter_session.greeter_stderr_log_path() == "/tmp/custom-greeter.log"
