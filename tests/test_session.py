# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026  Alexey Gladkov <legion@kernel.org>

from types import SimpleNamespace
import pwd

import wldm.session


def test_new_user_environ_merges_pam_and_user_fields(monkeypatch):
    monkeypatch.setattr(wldm.session.wldm.pam, "getenvlist",
                        lambda pamh: {"LANG": "C.UTF-8", "XDG_SESSION_TYPE": "wayland"})
    monkeypatch.setattr(wldm.session, "session_seat", lambda: "seat9")
    pw = pwd.struct_passwd(("alice", "x", 1001, 1001, "", "/home/alice", "/bin/bash"))
    ttydev = SimpleNamespace(number=12)

    env = wldm.session.new_user_environ(object(), pw, ttydev)

    assert env["HOME"] == "/home/alice"
    assert env["USER"] == "alice"
    assert env["LOGNAME"] == "alice"
    assert env["SHELL"] == "/bin/bash"
    assert env["TERM"] == "linux"
    assert env["XDG_RUNTIME_DIR"] == "/run/user/1001"
    assert env["XDG_SESSION_TYPE"] == "wayland"
    assert env["XDG_SESSION_CLASS"] == "user"
    assert env["XDG_SEAT"] == "seat9"
    assert env["XDG_VTNR"] == "12"
    assert env["LANG"] == "C.UTF-8"


def test_new_user_environ_exports_desktop_names(monkeypatch):
    monkeypatch.setattr(wldm.session.wldm.pam, "getenvlist", lambda pamh: {})
    monkeypatch.setattr(wldm.session, "session_seat", lambda: "seat0")
    monkeypatch.setattr(wldm.session, "session_desktop_names", lambda: ["KDE", "Plasma"])
    pw = pwd.struct_passwd(("alice", "x", 1001, 1001, "", "/home/alice", "/bin/bash"))

    env = wldm.session.new_user_environ(object(), pw)

    assert env["XDG_SESSION_DESKTOP"] == "KDE"
    assert env["XDG_CURRENT_DESKTOP"] == "KDE:Plasma"
    assert env["DESKTOP_SESSION"] == "KDE"


def test_session_hook_command_uses_config(monkeypatch):
    monkeypatch.setattr(
        wldm.session.wldm.config,
        "read_config",
        lambda: {
            "session": {
                "command": "default",
                "pre-command": "/usr/libexec/pre-hook",
                "post-command": "/usr/libexec/post-hook",
            }
        },
    )

    assert wldm.session.session_hook_command("pre") == "/usr/libexec/pre-hook"
    assert wldm.session.session_hook_command("post") == "/usr/libexec/post-hook"


def test_default_session_wrapper_uses_repo_script_when_progname_is_set(monkeypatch, tmp_path):
    launcher = tmp_path / "wldm.sh"
    launcher.write_text("", encoding="utf-8")

    monkeypatch.setenv("WLDM_PROGNAME", str(launcher))

    assert wldm.session.default_session_wrapper() == str(tmp_path / "scripts" / "wayland-session")


def test_default_session_wrapper_uses_installed_share_path(monkeypatch, tmp_path):
    monkeypatch.delenv("WLDM_PROGNAME", raising=False)
    monkeypatch.setattr(wldm.session.sys, "prefix", str(tmp_path))

    assert wldm.session.default_session_wrapper() == str(tmp_path / "share" / "wldm" / "scripts" / "wayland-session")


def test_session_wrapper_command_uses_default_wrapper(monkeypatch):
    monkeypatch.setattr(
        wldm.session.wldm.config,
        "read_config",
        lambda: {"session": {"command": "default"}},
    )
    monkeypatch.setattr(wldm.session, "default_session_wrapper", lambda: "/usr/share/wldm/scripts/wayland-session")

    assert wldm.session.session_wrapper_command() == ["/usr/share/wldm/scripts/wayland-session"]


def test_session_wrapper_command_can_disable_wrapper(monkeypatch):
    monkeypatch.setattr(
        wldm.session.wldm.config,
        "read_config",
        lambda: {"session": {"command": "none"}},
    )

    assert wldm.session.session_wrapper_command() == []


def test_session_exec_command_prepends_wrapper(monkeypatch):
    monkeypatch.setattr(wldm.session, "session_wrapper_command", lambda: ["wrapper", "--flag"])
    monkeypatch.setattr(wldm.session, "resolve_executable", lambda prog: f"/usr/bin/{prog}")

    assert wldm.session.session_exec_command(["/usr/bin/sway", "--debug"]) == [
        "/usr/bin/wrapper",
        "--flag",
        "/usr/bin/sway",
        "--debug",
    ]


def test_session_exec_command_fails_when_wrapper_is_missing(monkeypatch):
    monkeypatch.setattr(wldm.session, "session_wrapper_command", lambda: ["missing-wrapper"])
    monkeypatch.setattr(wldm.session, "resolve_executable", lambda prog: "")

    try:
        wldm.session.session_exec_command(["/usr/bin/sway"])
    except RuntimeError as exc:
        assert "session wrapper executable" in str(exc)
    else:
        raise AssertionError("session_exec_command() should have failed")


def test_run_session_hook_executes_command_as_user(monkeypatch):
    pw = pwd.struct_passwd(("alice", "x", 1001, 1001, "", "/home/alice", "/bin/bash"))
    ttydev = SimpleNamespace(filename="/dev/tty12")
    calls = {}

    monkeypatch.setattr(wldm.session.os, "getgrouplist", lambda user, gid: [gid, 27])

    def fake_run(cmd, check, cwd, env, user, group, extra_groups):
        calls["cmd"] = cmd
        calls["check"] = check
        calls["cwd"] = cwd
        calls["env"] = env
        calls["user"] = user
        calls["group"] = group
        calls["extra_groups"] = extra_groups
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(wldm.session.subprocess, "run", fake_run)

    result = wldm.session.run_session_hook(
        "pre",
        "/usr/libexec/pre-hook --flag",
        pw,
        {"HOME": pw.pw_dir},
        ttydev,
        "/usr/bin/sway --debug",
    )

    assert result is True
    assert calls["cmd"] == ["/usr/libexec/pre-hook", "--flag"]
    assert calls["cwd"] == "/home/alice"
    assert calls["env"]["WLDM_TTY"] == "/dev/tty12"
    assert calls["env"]["WLDM_SESSION_COMMAND"] == "/usr/bin/sway --debug"
    assert calls["user"] == 1001
    assert calls["group"] == 1001
    assert calls["extra_groups"] == [1001, 27]


def test_run_session_hook_reports_failure(monkeypatch):
    pw = pwd.struct_passwd(("alice", "x", 1001, 1001, "", "/home/alice", "/bin/bash"))
    ttydev = SimpleNamespace(filename="/dev/tty12")
    criticals = []

    monkeypatch.setattr(wldm.session.os, "getgrouplist", lambda user, gid: [gid])
    monkeypatch.setattr(
        wldm.session.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=7),
    )
    monkeypatch.setattr(
        wldm.session.logger,
        "critical",
        lambda msg, *args: criticals.append(msg % args if args else msg),
    )

    result = wldm.session.run_session_hook("pre", "/usr/libexec/pre-hook", pw, {}, ttydev, "/usr/bin/sway")

    assert result is False
    assert any("pre hook failed" in message for message in criticals)


def test_cmd_main_uses_user_shell_when_program_missing(monkeypatch):
    pw = pwd.struct_passwd(("alice", "x", 1001, 1001, "", "/home/alice", "/bin/bash"))
    calls = {}

    monkeypatch.setattr(wldm.session.pwd, "getpwnam", lambda username: pw)
    monkeypatch.setattr(wldm.session, "session_pam_service", lambda: "custom-login")
    monkeypatch.setattr(wldm.session.wldm.logindefs, "read_values", lambda: None)
    monkeypatch.setattr(wldm.session.os, "access", lambda path, mode: True)

    def fake_run_user_session(pw_arg, pam_service, prog_args):
        calls["pw"] = pw_arg
        calls["pam_service"] = pam_service
        calls["prog_args"] = prog_args

    monkeypatch.setattr(wldm.session, "run_user_session", fake_run_user_session)

    result = wldm.session.cmd_main(SimpleNamespace(username="alice", prog="", args=[]))

    assert result == wldm.session.wldm.EX_SUCCESS
    assert calls["pw"] == pw
    assert calls["pam_service"] == "custom-login"
    assert calls["prog_args"] == ["/bin/bash", "-l"]


def test_cmd_main_resolves_program_from_exec_path(monkeypatch):
    pw = pwd.struct_passwd(("alice", "x", 1001, 1001, "", "/home/alice", "/bin/bash"))
    calls = {}

    monkeypatch.setattr(wldm.session.pwd, "getpwnam", lambda username: pw)
    monkeypatch.setattr(wldm.session, "session_pam_service", lambda: "custom-login")
    monkeypatch.setattr(wldm.session.wldm.logindefs, "read_values", lambda: None)
    monkeypatch.setattr(wldm.session.os, "get_exec_path", lambda: ["/usr/bin", "/bin"])

    def fake_access(path, mode):
        return path == "/usr/bin/startplasma-wayland"

    monkeypatch.setattr(wldm.session.os, "access", fake_access)
    monkeypatch.setattr(
        wldm.session,
        "run_user_session",
        lambda pw_arg, pam_service, prog_args: calls.update(
            {"pw": pw_arg, "pam_service": pam_service, "prog_args": prog_args}
        ),
    )

    result = wldm.session.cmd_main(
        SimpleNamespace(username="alice", prog="startplasma-wayland", args=["--foo"])
    )

    assert result == wldm.session.wldm.EX_SUCCESS
    assert calls["pam_service"] == "custom-login"
    assert calls["prog_args"] == ["/usr/bin/startplasma-wayland", "--foo"]


def test_finish_user_session_always_ends_pam(monkeypatch):
    calls = []

    monkeypatch.setattr(wldm.session.wldm.pam, "close_pam_session",
                        lambda pamh: calls.append(("close", pamh)))
    monkeypatch.setattr(wldm.session.wldm.pam, "end_pam",
                        lambda pamh: calls.append(("end", pamh)))

    wldm.session.finish_user_session("handle")

    assert calls == [("close", "handle"), ("end", "handle")]


def test_finish_user_session_ends_pam_even_on_close_error(monkeypatch):
    calls = []

    def fail_close(pamh):
        calls.append(("close", pamh))
        raise RuntimeError("boom")

    monkeypatch.setattr(wldm.session.wldm.pam, "close_pam_session", fail_close)
    monkeypatch.setattr(wldm.session.wldm.pam, "end_pam",
                        lambda pamh: calls.append(("end", pamh)))

    wldm.session.finish_user_session("handle")

    assert calls == [("close", "handle"), ("end", "handle")]


def test_cmd_main_fails_for_unknown_user(monkeypatch):
    monkeypatch.setattr(
        wldm.session.pwd,
        "getpwnam",
        lambda username: (_ for _ in ()).throw(KeyError(username)),
    )

    result = wldm.session.cmd_main(SimpleNamespace(username="missing", prog="", args=[]))

    assert result == wldm.session.wldm.EX_FAILURE


def test_cmd_main_fails_when_program_cannot_be_found(monkeypatch):
    pw = pwd.struct_passwd(("alice", "x", 1001, 1001, "", "/home/alice", "/bin/bash"))

    monkeypatch.setattr(wldm.session.pwd, "getpwnam", lambda username: pw)
    monkeypatch.setattr(wldm.session.wldm.logindefs, "read_values", lambda: None)
    monkeypatch.setattr(wldm.session.os, "get_exec_path", lambda: ["/usr/bin", "/bin"])
    monkeypatch.setattr(wldm.session.os, "access", lambda path, mode: False)

    result = wldm.session.cmd_main(
        SimpleNamespace(username="alice", prog="missing-command", args=[])
    )

    assert result == wldm.session.wldm.EX_FAILURE


def test_run_user_session_returns_early_when_console_is_unavailable(monkeypatch):
    closed = []

    monkeypatch.setattr(wldm.session.wldm.tty, "open_console", lambda: None)
    monkeypatch.setattr(wldm.session.os, "close", lambda fd: closed.append(fd))

    pw = pwd.struct_passwd(("alice", "x", 1001, 1001, "", "/home/alice", "/bin/bash"))

    assert wldm.session.run_user_session(pw, "login", ["/bin/bash", "-l"]) is None
    assert closed == []


def test_run_user_session_parent_path_opens_and_closes_resources(monkeypatch):
    pw = pwd.struct_passwd(("alice", "x", 1001, 1001, "", "/home/alice", "/bin/bash"))
    calls = []
    ttydev_holder = {}

    class DummyTTY:
        fd = 55
        number = 12
        filename = "/dev/tty12"

        def __init__(self, console, uid):
            calls.append(("tty_init", console, uid))
            ttydev_holder["tty"] = self

        def switch(self):
            calls.append(("tty_switch",))

        def close(self):
            calls.append(("tty_close",))

    monkeypatch.setattr(wldm.session.wldm.tty, "open_console", lambda: 77)
    monkeypatch.setattr(wldm.session.wldm.tty, "TTYdevice", DummyTTY)
    monkeypatch.setattr(wldm.session, "prepare_user_terminal",
                        lambda ttydev: calls.append(("prepare_user_terminal", ttydev.fd)))
    monkeypatch.setattr(wldm.session.wldm.pam, "start_pam",
                        lambda service, user: calls.append(("start_pam", service, user)) or "pamh")
    monkeypatch.setattr(wldm.session.wldm.pam, "set_pam_item",
                        lambda pamh, item_type, value: calls.append(("set_pam_item", pamh, item_type, value)))
    monkeypatch.setattr(wldm.session.wldm.pam, "putenv",
                        lambda pamh, name, value: calls.append(("putenv", pamh, name, value)))
    monkeypatch.setattr(wldm.session.wldm.pam, "open_pam_session",
                        lambda pamh: calls.append(("open_pam_session", pamh)))
    monkeypatch.setattr(wldm.session, "new_user_environ",
                        lambda pamh, pw_arg, ttydev=None:
                        calls.append(("new_user_environ", pamh, pw_arg.pw_name)) or {"HOME": pw_arg.pw_dir})
    monkeypatch.setattr(wldm.session, "session_exec_command",
                        lambda prog_args: calls.append(("session_exec_command", prog_args)) or prog_args)
    monkeypatch.setattr(wldm.session, "session_hook_command",
                        lambda name: "/usr/libexec/pre-hook" if name == "pre" else "/usr/libexec/post-hook")
    monkeypatch.setattr(
        wldm.session,
        "run_session_hook",
        lambda name, command, pw_arg, env, ttydev, session_prog:
            calls.append(("run_session_hook", name, command, session_prog, env)) or True,
    )
    monkeypatch.setattr(wldm.session, "exec_user_program",
                        lambda ttydev, username, uid, gid, workdir, prog, prog_args, env:
                        calls.append(("exec_user_program", ttydev.fd, username, uid, gid, workdir, prog, prog_args, env)))
    monkeypatch.setattr(wldm.session.os, "fork", lambda: 1234)
    monkeypatch.setattr(wldm.session.os, "waitpid", lambda pid, flags: (pid, 0))
    monkeypatch.setattr(wldm.session.os, "WIFEXITED", lambda status: True)
    monkeypatch.setattr(wldm.session.os, "WEXITSTATUS", lambda status: 0)
    monkeypatch.setattr(wldm.session.os, "close", lambda fd: calls.append(("close_console", fd)))
    monkeypatch.setattr(wldm.session.wldm.wtmp, "login",
                        lambda tty_path, username, host="": calls.append(("wtmp_login", tty_path, username, host)))
    monkeypatch.setattr(wldm.session.wldm.wtmp, "logout",
                        lambda tty_path, host="": calls.append(("wtmp_logout", tty_path, host)))
    monkeypatch.setattr(wldm.session, "finish_user_session",
                        lambda pamh: calls.append(("finish_user_session", pamh)))

    wldm.session.run_user_session(pw, "custom-login", ["/bin/bash", "-l"])

    assert ("tty_init", 77, 1001) in calls
    assert ("prepare_user_terminal", 55) in calls
    assert ("start_pam", "custom-login", "alice") in calls
    assert ("set_pam_item", "pamh", wldm.session.wldm.pam.PAM_TTY, "/dev/tty12") in calls
    assert ("putenv", "pamh", "XDG_SESSION_TYPE", "wayland") in calls
    assert ("putenv", "pamh", "XDG_SESSION_CLASS", "user") in calls
    assert ("putenv", "pamh", "XDG_SEAT", "seat0") in calls
    assert ("putenv", "pamh", "XDG_VTNR", "12") in calls
    assert ("open_pam_session", "pamh") in calls
    assert ("session_exec_command", ["/bin/bash", "-l"]) in calls
    assert any(call[:3] == ("run_session_hook", "pre", "/usr/libexec/pre-hook") for call in calls)
    assert any(call[:3] == ("run_session_hook", "post", "/usr/libexec/post-hook") for call in calls)
    assert ("wtmp_login", "/dev/tty12", "alice", "") in calls
    assert ("wtmp_logout", "/dev/tty12", "") in calls
    assert ("tty_close",) in calls
    assert ("finish_user_session", "pamh") in calls
    assert ("close_console", 77) in calls
    assert all(call[0] != "exec_user_program" for call in calls)


def test_run_user_session_parent_path_logs_nonzero_exit(monkeypatch):
    pw = pwd.struct_passwd(("alice", "x", 1001, 1001, "", "/home/alice", "/bin/bash"))
    criticals = []

    class DummyTTY:
        filename = "/dev/tty12"
        number = 12

        def __init__(self, console, uid):
            self.fd = 55

        def switch(self):
            raise AssertionError("child path should not run in parent-path test")

        def close(self):
            return None

    monkeypatch.setattr(wldm.session.wldm.tty, "open_console", lambda: 77)
    monkeypatch.setattr(wldm.session.wldm.tty, "TTYdevice", DummyTTY)
    monkeypatch.setattr(wldm.session, "prepare_user_terminal", lambda ttydev: None)
    monkeypatch.setattr(wldm.session.wldm.pam, "start_pam", lambda service, user: "pamh")
    monkeypatch.setattr(wldm.session.wldm.pam, "set_pam_item", lambda pamh, item_type, value: None)
    monkeypatch.setattr(wldm.session.wldm.pam, "putenv", lambda pamh, name, value: None)
    monkeypatch.setattr(wldm.session.wldm.pam, "open_pam_session", lambda pamh: None)
    monkeypatch.setattr(wldm.session, "new_user_environ", lambda pamh, pw_arg, ttydev=None: {})
    monkeypatch.setattr(wldm.session, "session_exec_command", lambda prog_args: prog_args)
    monkeypatch.setattr(wldm.session, "session_hook_command", lambda name: "")
    monkeypatch.setattr(wldm.session, "run_session_hook", lambda *args, **kwargs: True)
    monkeypatch.setattr(wldm.session.os, "fork", lambda: 1234)
    monkeypatch.setattr(wldm.session.os, "waitpid", lambda pid, flags: (pid, 7))
    monkeypatch.setattr(wldm.session.os, "WIFEXITED", lambda status: True)
    monkeypatch.setattr(wldm.session.os, "WEXITSTATUS", lambda status: 7)
    monkeypatch.setattr(wldm.session.os, "close", lambda fd: None)
    monkeypatch.setattr(wldm.session, "finish_user_session", lambda pamh: None)
    monkeypatch.setattr(wldm.session.logger, "critical",
                        lambda msg, *args: criticals.append(msg % args if args else msg))

    wldm.session.run_user_session(pw, "login", ["/bin/bash", "-l"])

    assert any("Child exited" in message for message in criticals)


def test_run_user_session_aborts_when_pre_hook_fails(monkeypatch):
    pw = pwd.struct_passwd(("alice", "x", 1001, 1001, "", "/home/alice", "/bin/bash"))
    calls = []

    class DummyTTY:
        filename = "/dev/tty12"
        number = 12

        def __init__(self, console, uid):
            self.fd = 55

        def close(self):
            calls.append(("tty_close",))

    monkeypatch.setattr(wldm.session.wldm.tty, "open_console", lambda: 77)
    monkeypatch.setattr(wldm.session.wldm.tty, "TTYdevice", DummyTTY)
    monkeypatch.setattr(wldm.session, "prepare_user_terminal", lambda ttydev: None)
    monkeypatch.setattr(wldm.session, "open_user_pam_session", lambda pam_service, pw_arg, ttydev: wldm.session.contextlib.nullcontext("pamh"))
    monkeypatch.setattr(wldm.session, "new_user_environ", lambda pamh, pw_arg, ttydev=None: {"HOME": pw_arg.pw_dir})
    monkeypatch.setattr(wldm.session, "session_exec_command", lambda prog_args: prog_args)
    monkeypatch.setattr(wldm.session, "session_hook_command", lambda name: "/usr/libexec/pre-hook" if name == "pre" else "")
    monkeypatch.setattr(wldm.session, "run_session_hook", lambda *args, **kwargs: False)
    monkeypatch.setattr(wldm.session.os, "fork", lambda: (_ for _ in ()).throw(AssertionError("fork should not be called")))
    monkeypatch.setattr(wldm.session.os, "close", lambda fd: calls.append(("close_console", fd)))

    wldm.session.run_user_session(pw, "login", ["/bin/bash", "-l"])

    assert ("tty_close",) in calls
    assert ("close_console", 77) in calls


def test_prepare_user_terminal_switches_and_sets_controlling_tty(monkeypatch):
    calls = []

    class DummyTTY:
        fd = 55
        filename = "/dev/tty12"

        def switch(self):
            calls.append(("tty_switch",))

    monkeypatch.setattr(wldm.session.os, "setsid", lambda: calls.append(("setsid",)))
    monkeypatch.setattr(
        wldm.session.wldm.tty,
        "make_control_tty",
        lambda fd: calls.append(("make_control_tty", fd)) or True,
    )

    wldm.session.prepare_user_terminal(DummyTTY())

    assert calls == [
        ("tty_switch",),
        ("setsid",),
        ("make_control_tty", 55),
    ]


def test_session_pam_service_uses_config(monkeypatch):
    monkeypatch.setattr(
        wldm.session.wldm.config,
        "read_config",
        lambda: {"session": {"pam-service": "session-custom", "command": "default"}},
    )

    assert wldm.session.session_pam_service() == "session-custom"


def test_prepare_user_terminal_fails_when_tty_cannot_become_controlling(monkeypatch):
    class DummyTTY:
        fd = 55
        filename = "/dev/tty12"

        def switch(self):
            return None

    monkeypatch.setattr(wldm.session.os, "setsid", lambda: None)
    monkeypatch.setattr(wldm.session.wldm.tty, "make_control_tty", lambda fd: False)

    try:
        wldm.session.prepare_user_terminal(DummyTTY())
    except RuntimeError as exc:
        assert "/dev/tty12" in str(exc)
    else:
        raise AssertionError("prepare_user_terminal() should have failed")
