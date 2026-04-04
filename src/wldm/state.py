#!/usr/bin/env python
# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026  Alexey Gladkov <legion@kernel.org>

import os

import wldm
import wldm.inifile

logger = wldm.logger

LAST_SESSION_FILE = "last-session"
LAST_SESSION_MAX_FILE_SIZE = 4096
LAST_SESSION_ALLOWED = {"session": {"username", "command"}}


def last_session_path(state_dir: str) -> str:
    """Return the persistent greeter state file path for one state directory."""
    return os.path.join(state_dir, LAST_SESSION_FILE)


def load_last_session_file(path: str) -> tuple[str, str]:
    """Load the remembered username and session command from one state file.

    Args:
        path: Full path to the persistent `last-session` state file.

    Returns:
        A tuple `(username, command)` from the parsed state file, or empty
        strings when the file is missing or invalid.
    """
    if not path:
        return "", ""

    try:
        parsed = wldm.inifile.read_ini_file(
            path,
            allowed=LAST_SESSION_ALLOWED,
            max_size=LAST_SESSION_MAX_FILE_SIZE,
        )
    except FileNotFoundError:
        return "", ""
    except (OSError, RuntimeError, OverflowError, UnicodeError, wldm.inifile.IniParseError) as e:
        logger.warning("ignoring invalid last-session state in %s: %s", path, e)
        return "", ""

    return parsed.get_str("session", "username"), parsed.get_str("session", "command")


def save_last_session_file(path: str, username: str, command: str) -> None:
    """Persist the remembered username and session command to one state file.

    Args:
        path: Full path to the persistent `last-session` state file.
        username: Username that should be restored in the greeter.
        command: Session command that should be restored in the greeter.
    """
    if not path or not username or not command:
        return

    state_dir = os.path.dirname(path)
    wldm.ensure_secure_directory(state_dir, mode=0o700)

    tmp_path = f"{path}.tmp"

    with open(tmp_path, "w", encoding="utf-8") as fileobj:
        fileobj.write("[session]\n")
        fileobj.write(f"username = {username}\n")
        fileobj.write(f"command = {command}\n")

    os.replace(tmp_path, path)


def load_last_session(state_dir: str) -> tuple[str, str]:
    """Load the remembered username and session command from one state dir."""
    if not state_dir:
        return "", ""

    return load_last_session_file(last_session_path(state_dir))


def save_last_session(state_dir: str, username: str, command: str) -> None:
    """Persist the remembered username and session command in one state dir."""
    if not state_dir:
        return

    save_last_session_file(last_session_path(state_dir), username, command)
