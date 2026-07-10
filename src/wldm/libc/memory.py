# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026  Alexey Gladkov <legion@kernel.org>

import ctypes
from typing import Any

import wldm.libc._ffi as ffi

_calloc: Any | None = None
_free: Any | None = None
_strlen: Any | None = None


def calloc(count: int, size: int) -> Any:
    global _calloc

    if _calloc is None:
        _calloc = ffi.require_symbol("calloc")
        _calloc.argtypes = [ctypes.c_size_t, ctypes.c_size_t]
        _calloc.restype = ctypes.c_void_p

    return _calloc(count, size)


def free(ptr: Any) -> None:
    global _free

    if _free is None:
        _free = ffi.require_symbol("free")
        _free.argtypes = [ctypes.c_void_p]
        _free.restype = None

    _free(ptr)


def strlen(ptr: Any) -> int:
    global _strlen

    if _strlen is None:
        _strlen = ffi.require_symbol("strlen")
        _strlen.argtypes = [ctypes.c_char_p]
        _strlen.restype = ctypes.c_size_t

    return int(_strlen(ptr))
