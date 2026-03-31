#!/usr/bin/env python
# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026  Alexey Gladkov <legion@kernel.org>

import ctypes
import ctypes.util
from typing import Any, Optional


def _require_library(name: str) -> str:
    path = ctypes.util.find_library(name)
    if path is None:
        raise RuntimeError(f"unable to locate required library: {name}")
    return path


def _require_symbol(lib: Any, name: str) -> Any:
    if not hasattr(lib, name):
        raise RuntimeError(f"required libc symbol is missing: {name}")
    return getattr(lib, name)


_libc = ctypes.CDLL(_require_library("c"), use_errno=True)

_calloc_ptr = _require_symbol(_libc, "calloc")
_calloc_ptr.argtypes = [ctypes.c_size_t, ctypes.c_size_t]
_calloc_ptr.restype = ctypes.c_void_p
calloc: Any = _calloc_ptr

_free_ptr = _require_symbol(_libc, "free")
_free_ptr.argtypes = [ctypes.c_void_p]
_free_ptr.restype = None
free: Any = _free_ptr

_strlen_ptr = _require_symbol(_libc, "strlen")
_strlen_ptr.argtypes = [ctypes.c_char_p]
_strlen_ptr.restype = ctypes.c_size_t
strlen: Any = _strlen_ptr

if hasattr(_libc, "logwtmp"):
    _logwtmp_ptr = _libc.logwtmp
    _logwtmp_ptr.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p]
    _logwtmp_ptr.restype = None
    logwtmp: Optional[Any] = _logwtmp_ptr
else:
    logwtmp = None
