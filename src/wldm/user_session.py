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
from typing import Dict, Iterator, List, Optional, Any

import wldm
import wldm.config
import wldm.inifile
import wldm.pam
import wldm.policy
import wldm.tty
import wldm.logindefs
import wldm.wtmp

logger = wldm.logger


def validate_execute_path(name: str, execute: str) -> str:
    if not execute:
        return ""

    if not os.path.isabs(execute) or not os.access(execute, os.X_OK):
        raise RuntimeError(f"Could not find the {name} executable: {execute}")

    return execute


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
    env["XDG_SEAT"] = os.environ.get("WLDM_SEAT", wldm.policy.DEFAULT_SEAT)
    desktop_names = [item for item in os.environ.get("WLDM_SESSION_DESKTOP_NAMES", "").split(":") if item]
    if desktop_names:
        env["XDG_SESSION_DESKTOP"] = desktop_names[0]
        env["XDG_CURRENT_DESKTOP"] = ":".join(desktop_names)
        env["DESKTOP_SESSION"] = desktop_names[0]
    if ttydev is not None:
        env["XDG_VTNR"] = str(ttydev.number)

    return env


def run_session_hook(name: str,
                     execute: str,
                     pw: pwd.struct_passwd,
                     env: Dict[str, str],
                     ttydev: wldm.tty.TTYdevice,
                     session_command: str) -> bool:
    if not execute:
        return True

    hook_env = dict(
        env,
        WLDM_TTY=ttydev.filename,
        WLDM_SESSION_COMMAND=session_command,
    )
    extra_groups = os.getgrouplist(pw.pw_name, pw.pw_gid)

    result = subprocess.run(
        [execute],
        check=False,
        cwd=pw.pw_dir,
        env=hook_env,
        user=pw.pw_uid,
        group=pw.pw_gid,
        extra_groups=extra_groups,
    )
    if result.returncode == 0:
        return True

    logger.critical("[!] %s hook failed with status %d: %s", name, result.returncode, execute)
    return False


def process_exit_status(status: int) -> int:
    if os.WIFEXITED(status):
        return os.WEXITSTATUS(status)
    if os.WIFSIGNALED(status):
        return 128 + os.WTERMSIG(status)
    return wldm.EX_FAILURE


def build_session_argv(shell: str) -> List[str]:
    """Resolve the final session argv from the daemon-provided command string.

    Args:
        shell: User shell used when the session command needs shell syntax.

    Returns:
        Final argv for the session child.
    """
    session_command = os.environ.get("WLDM_SESSION_COMMAND", "").strip()
    if not session_command:
        raise RuntimeError("environ variable `WLDM_SESSION_COMMAND' not specified")

    prog, *args = shlex.split(session_command)

    if not prog:
        raise RuntimeError("Invalid session command: empty command")

    if not os.path.isabs(prog) or not os.access(prog, os.X_OK):
        return [shell, "-c", shlex.join([prog] + args)]

    return [prog] + args


def exec_user_program(ttydev: wldm.tty.TTYdevice,
                      username: str, uid: int, gid: int, workdir: str,
                      shell: str,
                      wrapper: str,
                      env: Dict[str, str]) -> None:
    os.dup2(ttydev.fd, 0)
    os.dup2(ttydev.fd, 1)
    os.dup2(ttydev.fd, 2)

    wldm.drop_privileges(username, uid, gid, workdir)

    prog_args = build_session_argv(shell)

    if wrapper:
        prog_args = [wrapper] + prog_args

    wldm.close_inherited_fds()

    os.execve(prog_args[0], prog_args, env)


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
        wldm.pam.putenv(pamh, "XDG_SEAT", os.environ.get("WLDM_SEAT", wldm.policy.DEFAULT_SEAT))
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
                     wrapper: str = "",
                     pre_execute: str = "",
                     post_execute: str = "") -> int:
    try:
        with open_console_fd() as console:
            logger.debug("[+] Opening free TTY device")
            ttydev = wldm.tty.TTYdevice(console, pw.pw_uid)
            wtmp_line: Optional[str] = None

            try:
                prepare_user_terminal(ttydev)

                with open_user_pam_session(pam_service, pw, ttydev) as pamh:
                    env = new_user_environ(pamh, pw, ttydev)
                    shell = pw.pw_shell or "/bin/sh"

                    if not run_session_hook("pre", pre_execute, pw, env, ttydev, env.get("WLDM_SESSION_COMMAND", "")):
                        return wldm.EX_FAILURE

                    pid = os.fork()

                    if pid == 0:
                        try:
                            exec_user_program(ttydev,
                                              pw.pw_name, pw.pw_uid, pw.pw_gid, pw.pw_dir,
                                              shell, wrapper, env)
                        except Exception as e:
                            logger.critical("[child] Failed to exec session command %r: %r",
                                            env.get("WLDM_SESSION_COMMAND", ""), e)
                            os._exit(1)
                    else:
                        wtmp_line = ttydev.filename
                        wldm.wtmp.login(wtmp_line, pw.pw_name)

                        _, status = os.waitpid(pid, 0)
                        exitcode = process_exit_status(status)

                        if exitcode != 0:
                            logger.critical("[+] Child exited. status=%s, exitcode=%s",
                                            status, exitcode)

                        run_session_hook("post", post_execute, pw, env, ttydev, env.get("WLDM_SESSION_COMMAND", ""))

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
    cfg = wldm.config.read_config()
    try:
        pw = pwd.getpwnam(parser.username)
    except KeyError:
        logger.critical("User '%s' not found.", parser.username)
        return wldm.EX_FAILURE

    wldm.logindefs.read_values()

    wrapper = cfg.get_str("session", "execute")
    pre_execute = cfg.get_str("session", "pre-execute")
    post_execute = cfg.get_str("session", "post-execute")

    try:
        return run_user_session(
            pw,
            cfg.get_str("session", "pam-service"),
            validate_execute_path("session wrapper", wrapper),
            validate_execute_path("pre hook", pre_execute),
            validate_execute_path("post hook", post_execute),
        )
    except RuntimeError as exc:
        logger.critical("[!] %s", exc)
        return wldm.EX_FAILURE
