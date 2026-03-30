#!/usr/bin/env python
# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026  Alexey Gladkov <legion@kernel.org>

import argparse
import contextlib
import os
import os.path
import pwd
import shlex
import subprocess
import sys

from typing import Dict, Iterator, List, Optional, Any

import wldm
import wldm.config
import wldm.pam
import wldm.policy
import wldm.tty
import wldm.logindefs
import wldm.wtmp

logger = wldm.logger


def session_seat() -> str:
    return os.environ.get("WLDM_SEAT", wldm.policy.DEFAULT_SEAT)


def session_desktop_names() -> List[str]:
    value = os.environ.get("WLDM_SESSION_DESKTOP_NAMES", "")
    return [item for item in value.split(":") if item]


def session_pam_service() -> str:
    cfg = wldm.config.read_config()
    return str(cfg["session"].get("pam-service", "login"))


def session_hook_command(name: str) -> str:
    cfg = wldm.config.read_config()
    return str(cfg["session"].get(f"{name}-command", "")).strip()


def resolve_executable(prog: str) -> str:
    if os.access(prog, os.X_OK):
        return prog

    for path in os.get_exec_path():
        prog_exec = os.path.join(path, prog)
        if os.access(prog_exec, os.X_OK):
            return prog_exec

    return ""


def default_session_wrapper() -> str:
    if "WLDM_PROGNAME" in os.environ:
        script_top = os.path.dirname(os.path.abspath(os.environ["WLDM_PROGNAME"]))
        return os.path.join(script_top, "scripts", "wayland-session")
    return os.path.join(sys.prefix, "share", "wldm", "scripts", "wayland-session")


def session_wrapper_command() -> List[str]:
    cfg = wldm.config.read_config()
    command = str(cfg["session"].get("command", "default")).strip()

    if command.lower() in ("", "none", "direct"):
        return []
    if command.lower() == "default":
        return [default_session_wrapper()]
    return shlex.split(command)


def session_exec_command(prog_args: List[str]) -> List[str]:
    wrapper = session_wrapper_command()
    if not wrapper:
        return prog_args
    wrapper_prog = resolve_executable(wrapper[0])
    if not wrapper_prog:
        raise RuntimeError(f"Could not find the session wrapper executable: {wrapper[0]}")
    wrapper[0] = wrapper_prog
    return wrapper + prog_args


def new_user_environ(pamh: Optional[Any],
                     pw: pwd.struct_passwd,
                     ttydev: Optional[wldm.tty.TTYdevice] = None) -> Dict[str, str]:
    env = {}

    if pamh is not None:
        for name, value in wldm.pam.getenvlist(pamh).items():
            logger.debug("[+] PAM env %s = %s", name, value)
            env[name] = value

    env["HOME"] = pw.pw_dir
    env["USER"] = pw.pw_name
    env["LOGNAME"] = pw.pw_name
    env["SHELL"] = pw.pw_shell or "/bin/sh"
    env["TERM"] = wldm.policy.DEFAULT_TERM
    env["XDG_RUNTIME_DIR"] = f"/run/user/{pw.pw_uid}"
    env["XDG_SESSION_TYPE"] = wldm.policy.SESSION_TYPE_WAYLAND
    env["XDG_SESSION_CLASS"] = wldm.policy.SESSION_CLASS_USER
    env["XDG_SEAT"] = session_seat()
    desktop_names = session_desktop_names()
    if desktop_names:
        env["XDG_SESSION_DESKTOP"] = desktop_names[0]
        env["XDG_CURRENT_DESKTOP"] = ":".join(desktop_names)
        env["DESKTOP_SESSION"] = desktop_names[0]
    if ttydev is not None:
        env["XDG_VTNR"] = str(ttydev.number)

    return env


def run_session_hook(name: str,
                     command: str,
                     pw: pwd.struct_passwd,
                     env: Dict[str, str],
                     ttydev: wldm.tty.TTYdevice,
                     session_prog: str) -> bool:
    if not command:
        return True

    hook_env = dict(
        env,
        WLDM_TTY=ttydev.filename,
        WLDM_SESSION_COMMAND=session_prog,
    )
    extra_groups = os.getgrouplist(pw.pw_name, pw.pw_gid)

    result = subprocess.run(
        shlex.split(command),
        check=False,
        cwd=pw.pw_dir,
        env=hook_env,
        user=pw.pw_uid,
        group=pw.pw_gid,
        extra_groups=extra_groups,
    )
    if result.returncode == 0:
        return True

    logger.critical("[!] %s hook failed with status %d: %s", name, result.returncode, command)
    return False


def process_exit_status(status: int) -> int:
    if os.WIFEXITED(status):
        return os.WEXITSTATUS(status)
    if os.WIFSIGNALED(status):
        return 128 + os.WTERMSIG(status)
    return wldm.EX_FAILURE


def exec_user_program(ttydev: wldm.tty.TTYdevice,
                      username: str, uid: int, gid: int, workdir: str,
                      prog: str, prog_args: List[str],
                      env: Dict[str, str]) -> None:
    # The tty becomes stdin/stdout/stderr
    os.dup2(ttydev.fd, 0)
    os.dup2(ttydev.fd, 1)
    os.dup2(ttydev.fd, 2)

    os.initgroups(username, gid)
    os.setgid(gid)
    os.setuid(uid)
    os.chdir(workdir)

    os.closerange(3, os.sysconf("SC_OPEN_MAX"))

    os.execve(prog, prog_args, env)


def prepare_user_terminal(ttydev: wldm.tty.TTYdevice) -> None:
    ttydev.switch()
    os.setsid()

    if not wldm.tty.make_control_tty(ttydev.fd):
        raise RuntimeError(f"unable to make {ttydev.filename} the controlling tty")


@contextlib.contextmanager
def open_console_fd() -> Iterator[int]:
    console = wldm.tty.open_console()
    if console is None:
        raise RuntimeError("Unable to open console")
    try:
        yield console
    finally:
        os.close(console)


@contextlib.contextmanager
def open_user_pam_session(pam_service: str,
                          pw: pwd.struct_passwd,
                          ttydev: wldm.tty.TTYdevice) -> Iterator[Any]:
    pamh = wldm.pam.start_pam(pam_service, pw.pw_name)
    try:
        wldm.pam.set_pam_item(pamh, wldm.pam.PAM_TTY, ttydev.filename)
        wldm.pam.putenv(pamh, "XDG_SESSION_TYPE", wldm.policy.SESSION_TYPE_WAYLAND)
        wldm.pam.putenv(pamh, "XDG_SESSION_CLASS", wldm.policy.SESSION_CLASS_USER)
        wldm.pam.putenv(pamh, "XDG_SEAT", session_seat())
        wldm.pam.putenv(pamh, "XDG_VTNR", str(ttydev.number))
        logger.debug("[+] PAM session starting for %s (service=%s)",
                     pw.pw_name, pam_service)
        wldm.pam.open_pam_session(pamh)
        logger.debug("[+] PAM session opened")
        yield pamh
    finally:
        finish_user_session(pamh)


def run_user_session(pw: pwd.struct_passwd,
                     pam_service: str,
                     prog_args: List[str]) -> int:
    try:
        with open_console_fd() as console:
            logger.debug("[+] Opening free TTY device")
            ttydev = wldm.tty.TTYdevice(console, pw.pw_uid)
            wtmp_line: Optional[str] = None
            try:
                prepare_user_terminal(ttydev)
                with open_user_pam_session(pam_service, pw, ttydev) as pamh:
                    env = new_user_environ(pamh, pw, ttydev)
                    exec_argv = session_exec_command(prog_args)
                    session_command = shlex.join(prog_args)
                    if not run_session_hook("pre", session_hook_command("pre"), pw, env, ttydev, session_command):
                        return wldm.EX_FAILURE
                    pid = os.fork()
                    if pid == 0:
                        try:
                            exec_user_program(ttydev,
                                              pw.pw_name, pw.pw_uid, pw.pw_gid, pw.pw_dir,
                                              exec_argv[0], exec_argv,
                                              env)
                        except Exception as e:
                            logger.critical("[child] Failed to exec `%s %s': %r",
                                            exec_argv[0], exec_argv, e)
                            os._exit(1)
                    else:
                        wtmp_line = ttydev.filename
                        wldm.wtmp.login(wtmp_line, pw.pw_name)
                        _, status = os.waitpid(pid, 0)
                        exitcode = process_exit_status(status)

                        if exitcode != 0:
                            logger.critical("[+] Child exited. status=%s, exitcode=%s",
                                            status, exitcode)
                        run_session_hook("post", session_hook_command("post"), pw, env, ttydev, session_command)
                        return exitcode
            finally:
                if wtmp_line is not None:
                    wldm.wtmp.logout(wtmp_line)
                ttydev.close()
    except RuntimeError as exc:
        logger.critical("[!] %s", exc)
        return wldm.EX_FAILURE

    return wldm.EX_FAILURE


def finish_user_session(pamh: Optional[Any]) -> None:
    if pamh is None:
        return
    try:
        logger.debug("[+] Closing PAM session...")
        wldm.pam.close_pam_session(pamh)
        logger.debug("[+] PAM session closed")
    except Exception as e:
        logger.critical("[!] Error closing PAM session: %s", e)
    finally:
        wldm.pam.end_pam(pamh)


def cmd_main(parser: argparse.Namespace) -> int:
    try:
        pw = pwd.getpwnam(parser.username)
    except KeyError:
        logger.critical("User '%s' not found.", parser.username)
        return wldm.EX_FAILURE

    wldm.logindefs.read_values()

    prog = parser.prog
    args = parser.args

    if len(prog) == 0:
        prog = pw.pw_shell or "/bin/sh"
        args = ["-l"]

    resolved_prog = resolve_executable(prog)
    if not resolved_prog:
        logger.critical("[!] Could not find the executable file: %s", prog)
        return wldm.EX_FAILURE
    prog = resolved_prog

    return run_user_session(pw, session_pam_service(), [prog] + args)
