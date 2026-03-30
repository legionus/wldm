#!/usr/bin/env python
# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026  Alexey Gladkov <legion@kernel.org>

import ctypes
import ctypes.util
import os
from typing import Any, Optional

import wldm

logger = wldm.logger

_libc_path = ctypes.util.find_library("c")
_libc = ctypes.CDLL(_libc_path, use_errno=True) if _libc_path else None

if _libc is not None and hasattr(_libc, "logwtmp"):
    _logwtmp_ptr = _libc.logwtmp
    _logwtmp_ptr.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p]
    _logwtmp_ptr.restype = None
    _logwtmp: Optional[Any] = _logwtmp_ptr
else:
    _logwtmp = None


def available() -> bool:
    return _logwtmp is not None


def tty_line(tty_path: str) -> str:
    return os.path.basename(tty_path)


def login(tty_path: str, username: str, host: str = "") -> None:
    if _logwtmp is None:
        logger.debug("wtmp support is not available")
        return

    _logwtmp(
        tty_line(tty_path).encode(),
        username.encode(),
        host.encode(),
    )


def logout(tty_path: str, host: str = "") -> None:
    if _logwtmp is None:
        logger.debug("wtmp support is not available")
        return

    _logwtmp(
        tty_line(tty_path).encode(),
        b"",
        host.encode(),
    )
