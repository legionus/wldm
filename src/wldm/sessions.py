#!/usr/bin/env python
# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026  Alexey Gladkov <legion@kernel.org>

import configparser
import os
import os.path
import pwd

from typing import Any, Dict, List

import wldm
import wldm.policy

logger = wldm.logger


def parse_desktop_names(value: str) -> List[str]:
    return [item for item in value.split(";") if item]


def user_sessions_enabled() -> bool:
    value = os.environ.get("WLDM_GREETER_USER_SESSIONS", "yes").strip().lower()
    return value not in ["0", "false", "no", "off"]


def configured_system_session_dirs() -> List[str]:
    value = os.environ.get("WLDM_GREETER_SESSION_DIRS", "")
    if value:
        return [item for item in value.split(":") if item]
    return list(wldm.policy.SYSTEM_WAYLAND_SESSION_DIRS)


def configured_user_session_dir() -> str:
    return os.environ.get("WLDM_GREETER_USER_SESSION_DIR", wldm.policy.USER_WAYLAND_SESSION_DIR)


def session_data_dirs(username: str = "") -> List[str]:
    datadirs = configured_system_session_dirs()

    if not user_sessions_enabled() or not username:
        return datadirs

    try:
        pw = pwd.getpwnam(username)
    except KeyError:
        return datadirs

    datadirs.insert(0, os.path.join(pw.pw_dir, configured_user_session_dir()))
    return datadirs


def read_desktop_sessions(datadirs: List[str]) -> List[Dict[str, Any]]:
    sessions_by_name: Dict[str, Dict[str, Any]] = {}

    for datadir in datadirs:
        try:
            with os.scandir(datadir) as it:
                for entry in it:
                    if not entry.is_file() or not entry.name.endswith(".desktop"):
                        continue

                    path = os.path.join(datadir, entry.name)
                    desktop = configparser.ConfigParser()
                    try:
                        with wldm.open_regular_text_file(path, max_size=wldm.policy.SESSION_ENTRY_MAX_FILE_SIZE) as f:
                            desktop.read_file(f)
                    except (OSError, RuntimeError, UnicodeError, configparser.Error) as e:
                        logger.warning("ignoring invalid wayland session entry %s: %s", path, e)
                        continue

                    entry_type = desktop.get('Desktop Entry', 'type', fallback='').lower()
                    entry_name = desktop.get('Desktop Entry', 'name', fallback='')
                    entry_exec = desktop.get('Desktop Entry', 'exec', fallback='')
                    entry_comment = desktop.get('Desktop Entry', 'comment', fallback='')
                    entry_desktop_names = parse_desktop_names(
                        desktop.get('Desktop Entry', 'DesktopNames', fallback='')
                    )

                    if entry_type != 'application' or not entry_name or not entry_exec:
                        continue

                    if not entry_desktop_names:
                        entry_desktop_names = [os.path.splitext(entry.name)[0]]

                    sessions_by_name.setdefault(entry_name, {
                        "name": entry_name,
                        "command": entry_exec,
                        "comment": entry_comment,
                        "desktop_names": entry_desktop_names,
                    })
        except OSError as e:
            logger.warning("unable to read wayland sessions from %s: %s", datadir, e)

    return [sessions_by_name[name] for name in sorted(sessions_by_name)]


def desktop_sessions(username: str = "") -> List[Dict[str, Any]]:
    return read_desktop_sessions(session_data_dirs(username))
