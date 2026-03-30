#!/usr/bin/env python
# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026  Alexey Gladkov <legion@kernel.org>

import argparse
import contextlib
import os
import os.path
import pwd

from typing import Dict, Iterator, List, Optional, Any

import wldm
import wldm.config
import wldm.pam
import wldm.tty
import wldm.logindefs
import wldm.wtmp

logger = wldm.logger


def session_seat() -> str:
    return os.environ.get("WLDM_SEAT", "seat0")


def session_pam_service() -> str:
    cfg = wldm.config.read_config()
    return str(cfg["session"].get("pam-service", "login"))


def new_user_environ(pamh: Optional[Any],
                     pw: pwd.struct_passwd) -> Dict[str, str]:
    env = {}

    if pamh is not None:
        for name, value in wldm.pam.getenvlist(pamh).items():
            logger.debug("[+] PAM env %s = %s", name, value)
            env[name] = value

    env["HOME"] = pw.pw_dir
    env["USER"] = pw.pw_name
    env["LOGNAME"] = pw.pw_name
    env["TERM"] = "linux"
    env["XDG_RUNTIME_DIR"] = f"/run/user/{pw.pw_uid}"

    return env


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
        wldm.pam.putenv(pamh, "XDG_SESSION_TYPE", "wayland")
        wldm.pam.putenv(pamh, "XDG_SESSION_CLASS", "user")
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
                     prog: str, prog_args: List[str]) -> None:
    try:
        with open_console_fd() as console:
            logger.debug("[+] Opening free TTY device")
            ttydev = wldm.tty.TTYdevice(console, pw.pw_uid)
            wtmp_line: Optional[str] = None
            try:
                prepare_user_terminal(ttydev)
                with open_user_pam_session(pam_service, pw, ttydev) as pamh:
                    pid = os.fork()
                    if pid == 0:
                        try:
                            exec_user_program(ttydev,
                                              pw.pw_name, pw.pw_uid, pw.pw_gid, pw.pw_dir,
                                              prog, prog_args,
                                              new_user_environ(pamh, pw))
                        except Exception as e:
                            logger.critical("[child] Failed to exec `%s %s': %r",
                                            prog, prog_args, e)
                            os._exit(1)
                    else:
                        wtmp_line = ttydev.filename
                        wldm.wtmp.login(wtmp_line, pw.pw_name)
                        _, status = os.waitpid(pid, 0)
                        exitcode = os.WEXITSTATUS(status) if os.WIFEXITED(status) else None

                        if exitcode and exitcode != 0:
                            logger.critical("[+] Child exited. status=%s, exitcode=%s",
                                            status, exitcode)
            finally:
                if wtmp_line is not None:
                    wldm.wtmp.logout(wtmp_line)
                ttydev.close()
    except RuntimeError:
        logger.critical("[!] Unable to open console")
        return None


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

    if not os.access(prog, os.X_OK):
        for path in os.get_exec_path():
            prog_exec = os.path.join(path, prog)
            if os.access(prog_exec, os.X_OK):
                prog = prog_exec
                break
        if not os.access(prog, os.X_OK):
            logger.critical("[!] Could not find the executable file: %s", prog)
            return wldm.EX_FAILURE

    run_user_session(pw, session_pam_service(), prog, [prog] + args)

    return wldm.EX_SUCCESS
