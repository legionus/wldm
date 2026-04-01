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
    return os.path.join(state_dir, LAST_SESSION_FILE)


def load_last_session(state_dir: str) -> tuple[str, str]:
    if not state_dir:
        return "", ""

    try:
        parsed = wldm.inifile.read_ini_file(
            last_session_path(state_dir),
            allowed=LAST_SESSION_ALLOWED,
            max_size=LAST_SESSION_MAX_FILE_SIZE,
        )
    except FileNotFoundError:
        return "", ""
    except (OSError, RuntimeError, OverflowError, UnicodeError, wldm.inifile.IniParseError) as e:
        logger.warning("ignoring invalid last-session state in %s: %s", state_dir, e)
        return "", ""

    return parsed.get_str("session", "username"), parsed.get_str("session", "command")


def save_last_session(state_dir: str, username: str, command: str) -> None:
    if not state_dir or not username or not command:
        return

    wldm.ensure_secure_directory(state_dir, mode=0o700)

    path = last_session_path(state_dir)
    tmp_path = f"{path}.tmp"

    with open(tmp_path, "w", encoding="utf-8") as fileobj:
        fileobj.write("[session]\n")
        fileobj.write(f"username = {username}\n")
        fileobj.write(f"command = {command}\n")

    os.replace(tmp_path, path)
