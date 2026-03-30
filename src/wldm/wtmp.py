#!/usr/bin/env python
# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026  Alexey Gladkov <legion@kernel.org>

import os

import wldm
from wldm._libc import logwtmp as libc_logwtmp

logger = wldm.logger

_logwtmp = libc_logwtmp


def available() -> bool:
    return _logwtmp is not None


def tty_line(tty_path: str) -> str:
    return os.path.basename(tty_path)


def login(tty_path: str, username: str, host: str = "") -> None:
    if _logwtmp is None:
        logger.debug("wtmp support is not available")
        return

    _logwtmp(tty_line(tty_path).encode(), username.encode(), host.encode())


def logout(tty_path: str, host: str = "") -> None:
    if _logwtmp is None:
        logger.debug("wtmp support is not available")
        return

    _logwtmp(tty_line(tty_path).encode(), b"", host.encode())
