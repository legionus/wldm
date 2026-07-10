#!/usr/bin/env python
# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026  Alexey Gladkov <legion@kernel.org>

import os

import wldm
import wldm.libc.wtmp as libc_wtmp

logger = wldm.logger

_logwtmp = libc_wtmp.logwtmp


def available() -> bool:
    if _logwtmp is None:
        return False

    if _logwtmp is libc_wtmp.logwtmp:
        return libc_wtmp.available()

    return True


def tty_line(tty_path: str) -> str:
    return os.path.basename(tty_path)


def login(tty_path: str, username: str, host: str = "") -> None:
    if _logwtmp is None:
        logger.debug("wtmp support is not available")
        return

    if _logwtmp(tty_line(tty_path).encode(), username.encode(), host.encode()) is False:
        logger.debug("wtmp support is not available")


def logout(tty_path: str, host: str = "") -> None:
    if _logwtmp is None:
        logger.debug("wtmp support is not available")
        return

    if _logwtmp(tty_line(tty_path).encode(), b"", host.encode()) is False:
        logger.debug("wtmp support is not available")
