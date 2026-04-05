#!/usr/bin/env python
# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026  Alexey Gladkov <legion@kernel.org>

import os
import stat
import sys
from typing import Any

import wldm

logger = wldm.logger

ROLE_ALLOWED_CTYPES = {
    "daemon": (None, "pam", "libpam.so", "libpam.so.", "c", "libc.so", "libc.so."),
    "greeter": (None, "gtk-4", "libgtk-4.so", "libgtk-4.so.", "c", "libc.so", "libc.so."),
    "greeter-session": (None, "pam", "libpam.so", "libpam.so.", "c", "libc.so", "libc.so."),
    "user-session": (None, "pam", "libpam.so", "libpam.so.", "c", "libc.so", "libc.so."),
    "dbus-adapter": (None,),
}

_installed_roles: set[str] = set()
SYSTEM_LIBRARY_DIRS = (
    "/lib/",
    "/lib64/",
    "/usr/lib/",
    "/usr/lib64/",
)


def is_trusted_system_library_path(path: str) -> bool:
    """Check whether one absolute library path looks like trusted system data.

    Args:
        path: Absolute library path from a ``ctypes.dlopen`` audit event.

    Returns:
        ``True`` when the path resolves inside a trusted system library
        directory and the file is root-owned and not writable by non-root.
    """
    if not path or not os.path.isabs(path):
        return False

    real_path = os.path.realpath(path)

    if not any(real_path.startswith(prefix) for prefix in SYSTEM_LIBRARY_DIRS):
        return False

    try:
        st = os.stat(real_path)
    except OSError:
        return False

    if not stat.S_ISREG(st.st_mode) or st.st_uid != 0 or st.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        return False

    return True


def is_allowed_ctypes_target(role: str, target: Any) -> bool:
    """Check whether a ctypes library load is expected for one process role.

    Args:
        role: Current wldm process role.
        target: Raw library target from a ``ctypes.dlopen`` audit event.

    Returns:
        ``True`` when the target matches the role allowlist, ``False``
        otherwise.
    """
    allowed_targets = ROLE_ALLOWED_CTYPES.get(role, ())

    if target is None:
        return None in allowed_targets

    if not isinstance(target, str):
        return False

    normalized = os.path.basename(target)

    for allowed in allowed_targets:
        if allowed is None:
            continue

        if normalized != allowed and not normalized.startswith(allowed):
            continue

        if os.path.isabs(target):
            return is_trusted_system_library_path(target)

        return True

    return False


def setup_audit_hook(role: str) -> None:
    """Install the role-specific audit hook once in the current process.

    Args:
        role: Current wldm process role.
    """
    if role in _installed_roles:
        return

    def hook(event: str, args: tuple[Any, ...]) -> None:
        if event != "ctypes.dlopen" or not args:
            return

        target = args[0]

        if is_allowed_ctypes_target(role, target):
            return

        logger.critical("audit denied unexpected ctypes load in %s: %r", role, target)
        raise RuntimeError(f"unexpected ctypes library load in {role}: {target!r}")

    sys.addaudithook(hook)

    _installed_roles.add(role)
