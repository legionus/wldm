# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026  Alexey Gladkov <legion@kernel.org>

import ctypes
from typing import Any

import wldm.libc._ffi as ffi

_missing = object()
_logwtmp: Any | None | object = _missing


def _logwtmp_func() -> Any | None:
    global _logwtmp

    if _logwtmp is _missing:
        binding = ffi.libc()
        if hasattr(binding, "logwtmp"):
            _logwtmp = binding.logwtmp
            _logwtmp.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p]
            _logwtmp.restype = None
        else:
            _logwtmp = None

    return None if _logwtmp is _missing else _logwtmp


def logwtmp(line: bytes, user: bytes, host: bytes) -> bool:
    func = _logwtmp_func()
    if func is None:
        return False

    func(line, user, host)
    return True
