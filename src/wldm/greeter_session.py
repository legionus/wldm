#!/usr/bin/env python
# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026  Alexey Gladkov <legion@kernel.org>

import argparse
import contextlib
import grp
import os
import os.path
import pwd

from typing import Dict, Iterator, List, Optional, Any

import wldm
import wldm.pam
import wldm.policy
import wldm.tty

logger = wldm.logger


def base_greeter_environ() -> Dict[str, str]:
    env: Dict[str, str] = {}

    for name, value in os.environ.items():
        if (name in ["PATH", "LANG", "LANGUAGE"]
                or name.startswith("LC_")
                or name.startswith("WLDM_")
                or name.startswith("XKB_DEFAULT_")):
            env[name] = value

    return env


def new_greeter_environ(pamh: Optional[Any],
                        pw: pwd.struct_passwd) -> Dict[str, str]:
    env = base_greeter_environ()

    if pamh is not None:
        for name, value in wldm.pam.getenvlist(pamh).items():
            logger.debug("[+] PAM env %s = %s", name, value)
            env[name] = value

    env["HOME"] = pw.pw_dir
    env["USER"] = pw.pw_name
    env["LOGNAME"] = pw.pw_name
    env["TERM"] = wldm.policy.DEFAULT_TERM
    env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{pw.pw_uid}")

    return env


def redirect_greeter_stderr(log_path: Optional[str] = None) -> None:
    if log_path is None:
        log_path = os.environ.get("WLDM_GREETER_STDERR_LOG", "/tmp/wldm/greeter.log")
    logfile = wldm.open_secure_append_file(log_path, mode=0o600)
    logfd = logfile.fileno()
    os.dup2(logfd, 2)
    logfile.close()


def log_greeter_diag(message: str, *args: Any) -> None:
    text = message % args if args else message
    print(f"[wldm] {text}", file=os.fdopen(os.dup(2), "w", encoding="utf-8", buffering=1))


def log_exec_environment(env: Dict[str, str], uid: int, gid: int) -> None:
    log_greeter_diag("target uid=%d gid=%d", uid, gid)
    for name in ["XDG_SEAT", "XDG_VTNR", "XDG_SESSION_TYPE", "XDG_SESSION_CLASS"]:
        if name in env:
            log_greeter_diag("exec env %s=%s", name, env[name])
        else:
            log_greeter_diag("exec env %s is unset", name)


def exec_greeter_program(username: str,
                         uid: int,
                         gid: int,
                         workdir: str,
                         prog: str,
                         prog_args: List[str],
                         env: Dict[str, str]) -> None:
    redirect_greeter_stderr()
    log_exec_environment(env, uid, gid)

    os.initgroups(username, gid)
    os.setgid(gid)
    os.setuid(uid)
    os.chdir(workdir)

    os.closerange(3, os.sysconf("SC_OPEN_MAX"))
    os.execve(prog, prog_args, env)


def prepare_greeter_terminal(ttydev: wldm.tty.TTYdevice) -> None:
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
def open_greeter_pam_session(pam_service: str,
                             pw: pwd.struct_passwd,
                             ttydev: wldm.tty.TTYdevice) -> Iterator[Any]:
    pamh = wldm.pam.start_pam(pam_service, pw.pw_name)
    try:
        wldm.pam.set_pam_item(pamh, wldm.pam.PAM_TTY, ttydev.filename)
        wldm.pam.putenv(pamh, "XDG_SESSION_TYPE", wldm.policy.SESSION_TYPE_WAYLAND)
        wldm.pam.putenv(pamh, "XDG_SESSION_CLASS", wldm.policy.SESSION_CLASS_GREETER)
        wldm.pam.putenv(pamh, "XDG_SEAT", os.environ.get("WLDM_SEAT", wldm.policy.DEFAULT_SEAT))
        wldm.pam.putenv(pamh, "XDG_VTNR", str(ttydev.number))
        logger.debug("[+] Greeter PAM session starting for %s (service=%s)",
                     pw.pw_name, pam_service)
        wldm.pam.open_pam_session_only(pamh)
        log_greeter_diag("opened PAM session for user=%s service=%s tty=%s",
                         pw.pw_name, pam_service, ttydev.filename)
        yield pamh
    finally:
        finish_greeter_session(pamh)


def finish_greeter_session(pamh: Optional[Any]) -> None:
    if pamh is None:
        return
    try:
        logger.debug("[+] Closing greeter PAM session...")
        wldm.pam.close_pam_session(pamh)
        logger.debug("[+] Greeter PAM session closed")
    except Exception as e:
        logger.critical("[!] Error closing greeter PAM session: %s", e)
    finally:
        wldm.pam.end_pam(pamh)


def run_greeter_session(pw: pwd.struct_passwd,
                        gid: int,
                        tty_number: int,
                        pam_service: str,
                        prog: str,
                        prog_args: List[str]) -> int:
    redirect_greeter_stderr()

    try:
        with open_console_fd() as console:
            ttydev = wldm.tty.TTYdevice(console, pw.pw_uid, number=tty_number)
            prepare_greeter_terminal(ttydev)

            with open_greeter_pam_session(pam_service, pw, ttydev) as pamh:
                try:
                    exec_greeter_program(
                        pw.pw_name, pw.pw_uid, gid, pw.pw_dir,
                        prog, prog_args,
                        new_greeter_environ(pamh, pw),
                    )
                except Exception as e:
                    logger.critical("Failed to exec `%s %s': %r", prog, prog_args, e)
                    return wldm.EX_FAILURE
            return wldm.EX_SUCCESS
    except RuntimeError as e:
        logger.critical("[!] %s", e)
        return wldm.EX_FAILURE
    except Exception:
        logger.exception("unexpected greeter session failure")
        return wldm.EX_FAILURE


def cmd_main(parser: argparse.Namespace) -> int:
    try:
        pw = pwd.getpwnam(parser.username)
    except KeyError:
        logger.critical("User '%s' not found.", parser.username)
        return wldm.EX_FAILURE

    try:
        gid = grp.getgrnam(parser.group).gr_gid
    except KeyError:
        logger.critical("Group '%s' not found.", parser.group)
        return wldm.EX_FAILURE

    prog = parser.prog
    args = parser.args

    if not os.access(prog, os.X_OK):
        for path in os.get_exec_path():
            prog_exec = os.path.join(path, prog)
            if os.access(prog_exec, os.X_OK):
                prog = prog_exec
                break
        if not os.access(prog, os.X_OK):
            logger.critical("[!] Could not find the executable file: %s", prog)
            return wldm.EX_FAILURE

    return run_greeter_session(
        pw,
        gid,
        parser.tty,
        parser.pam_service,
        prog,
        [prog] + args,
    )
