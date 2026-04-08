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
import wldm.command as wldm_command
import wldm.pam
import wldm.policy
import wldm.tty

logger = wldm.logger


def load_unprivileged_modules() -> tuple[Any]:
    """Import modules that are only needed after dropping privileges.

    Returns:
        A tuple with modules used exclusively in the unprivileged greeter exec
        path.
    """
    import shlex

    return (shlex,)


def _base_greeter_environ() -> Dict[str, str]:
    env: Dict[str, str] = {}

    for name, value in os.environ.items():
        if (name in ["PATH", "PYTHONPATH", "LANG", "LANGUAGE"]
                or name.startswith("LC_")
                or name.startswith("WLDM_")
                or name.startswith("XKB_DEFAULT_")):
            env[name] = value

    return env


def new_greeter_environ(pamh: Optional[Any],
                        pw: pwd.struct_passwd) -> Dict[str, str]:
    env = _base_greeter_environ()

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
    """Redirect greeter stderr to the configured log file when requested."""
    if log_path is None:
        log_path = os.environ.get("WLDM_GREETER_STDERR_LOG", "/tmp/wldm/greeter.log")

    log_path = log_path.strip()
    if not log_path:
        return

    logfile = wldm.open_secure_append_file(log_path, mode=0o600)
    logfd = logfile.fileno()

    os.dup2(logfd, 2)

    logfile.close()


def greeter_ipc_fd() -> int:
    socket_fd = os.environ.get("WLDM_SOCKET_FD", "").strip()
    if not socket_fd:
        raise RuntimeError("environ variable `WLDM_SOCKET_FD' not specified")

    fd = int(socket_fd)
    os.set_inheritable(fd, True)
    return fd


def build_greeter_argv() -> List[str]:
    """Resolve the final greeter argv from the daemon-provided command string.

    Returns:
        Final argv used to start the compositor wrapper and greeter process.
    """
    command = os.environ.get("WLDM_GREETER_COMMAND", "").strip()
    if not command:
        raise RuntimeError("environ variable `WLDM_GREETER_COMMAND' not specified")

    (shlex,) = load_unprivileged_modules()

    try:
        argv = shlex.split(command)
    except ValueError as exc:
        raise RuntimeError(f"invalid greeter command: {exc}") from exc

    if not argv:
        raise RuntimeError("invalid greeter command: empty command")

    return [*argv, *wldm_command.internal_command_prefix(), "greeter"]


def process_exit_status(status: int) -> int:
    if os.WIFEXITED(status):
        return os.WEXITSTATUS(status)
    if os.WIFSIGNALED(status):
        return 128 + os.WTERMSIG(status)
    return wldm.EX_FAILURE


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

        logger.info("opened PAM session for user=%s service=%s tty=%s",
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
                        pam_service: str,
                        tty_number: int) -> int:
    ipc_fd = greeter_ipc_fd()

    try:
        with open_console_fd() as console:
            ttydev = wldm.tty.TTYdevice(console, pw.pw_uid, number=tty_number)

            try:
                prepare_greeter_terminal(ttydev)

                with open_greeter_pam_session(pam_service, pw, ttydev) as pamh:
                    env = new_greeter_environ(pamh, pw)
                    prog_args = build_greeter_argv()

                    pid = os.fork()

                    if pid == 0:
                        try:
                            os.dup2(ttydev.fd, 0)
                            os.dup2(ttydev.fd, 1)

                            wldm.drop_privileges(pw.pw_name, pw.pw_uid, gid, pw.pw_dir)
                            wldm.close_inherited_fds((ipc_fd,))

                            os.execvpe(prog_args[0], prog_args, env)

                        except Exception as e:
                            logger.critical(
                                "failed to exec greeter command on %s as user=%s gid=%d cwd=%s argv=%r: %r",
                                ttydev.filename, pw.pw_name, gid, pw.pw_dir, prog_args, e,
                            )
                            os._exit(1)

                    os.close(ipc_fd)
                    ipc_fd = -1

                    _, status = os.waitpid(pid, 0)

                    return process_exit_status(status)

            finally:
                ttydev.close()

    except RuntimeError as e:
        logger.critical("[!] %s", e)
        return wldm.EX_FAILURE

    finally:
        if ipc_fd >= 0:
            os.close(ipc_fd)

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

    redirect_greeter_stderr()

    try:
        return run_greeter_session(pw, gid, parser.pam_service, parser.tty)

    except Exception as e:
        logger.exception("unexpected greeter session failure for user=%s group=%s tty=%s pam-service=%s: %s",
                         parser.username, parser.group, parser.tty, parser.pam_service, e)
        return wldm.EX_FAILURE
