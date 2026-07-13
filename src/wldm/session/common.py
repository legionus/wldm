# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026  Alexey Gladkov <legion@kernel.org>

import contextlib
import os
from typing import Any, Dict, Iterator, Optional

import wldm
import wldm.pam
import wldm.tty

logger = wldm.logger


def pam_environment(pamh: Optional[Any]) -> Dict[str, str]:
    """Return environment variables exported by an opened PAM handle."""
    env: Dict[str, str] = {}

    if pamh is None:
        return env

    for name, value in wldm.pam.getenvlist(pamh).items():
        logger.debug("[+] PAM env %s = %s", name, value)
        env[name] = value

    return env


def prepare_terminal(ttydev: wldm.tty.TTYdevice) -> None:
    """Switch to a tty and make it the controlling terminal."""
    ttydev.switch()
    os.setsid()

    if not wldm.tty.make_control_tty(ttydev.fd):
        raise RuntimeError(f"unable to make {ttydev.filename} the controlling tty")


@contextlib.contextmanager
def open_console_fd() -> Iterator[int]:
    """Open the console device and close it when the caller is done."""
    console = wldm.tty.open_console()
    if console is None:
        raise RuntimeError("Unable to open console")

    try:
        yield console
    finally:
        os.close(console)


def close_pam_session(pamh: Optional[Any], label: str) -> None:
    """Close and end a PAM session, preserving cleanup on close errors."""
    if pamh is None:
        return

    try:
        logger.debug("[+] Closing %s...", label)
        wldm.pam.close_pam_session(pamh)
        logger.debug("[+] %s closed", label)
    except Exception as exc:
        logger.critical("[!] Error closing %s: %s", label, exc)
    finally:
        wldm.pam.end_pam(pamh)
