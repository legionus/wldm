# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026  Alexey Gladkov <legion@kernel.org>

from typing import Callable, TypeVar, cast

import wldm

_T = TypeVar("_T")
_MISSING = object()


def unprivileged_loader(loader: Callable[[], _T]) -> Callable[[], _T]:
    """Cache a module loader that must only run after dropping privileges.

    Args:
        loader: Function that imports and returns the needed module wrapper.

    Returns:
        A zero-argument loader that preserves the wrapped return type, rejects
        privileged calls, and imports the modules at most once.
    """
    cached: object = _MISSING

    @wldm.require_unprivileged
    def wrapper() -> _T:
        nonlocal cached

        if cached is _MISSING:
            cached = loader()

        return cast(_T, cached)

    return wrapper
