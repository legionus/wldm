# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026  Alexey Gladkov <legion@kernel.org>

import ctypes
import ctypes.util
from typing import Any

_libc: ctypes.CDLL | None = None


def require_library(name: str) -> str:
    path = ctypes.util.find_library(name)
    if path is None:
        raise RuntimeError(f"unable to locate required library: {name}")
    return path


def libc() -> ctypes.CDLL:
    """Return the process-local libc binding, loading it on first use."""
    global _libc

    if _libc is None:
        _libc = ctypes.CDLL(require_library("c"), use_errno=True)

    return _libc


def require_symbol(name: str) -> Any:
    binding = libc()
    if not hasattr(binding, name):
        raise RuntimeError(f"required libc symbol is missing: {name}")
    return getattr(binding, name)
