#!/usr/bin/env python
# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026  Alexey Gladkov <legion@kernel.org>

import os
import os.path
import sys
import pwd
import grp
import shlex

import wldm
import wldm.inifile
import wldm.policy


def _config_candidates() -> list[str]:
    candidates: list[str] = []

    if "WLDM_CONFIG" in os.environ:
        candidates.append(os.environ["WLDM_CONFIG"])

    candidates.append(os.path.join(sys.prefix, "share", "wldm", "config", "wldm.ini"))
    candidates.append("/etc/wldm.ini")

    return candidates


def _resolve_command_path(command: str, base_dir: str) -> str:
    if not command:
        return ""

    parts = shlex.split(command)
    if not parts:
        return ""

    prog = parts[0]
    if os.path.isabs(prog):
        parts[0] = os.path.realpath(prog)
    elif os.path.dirname(prog):
        parts[0] = os.path.realpath(os.path.join(base_dir, prog))

    return shlex.join(parts)


def read_config() -> wldm.inifile.IniFile:
    ent_pw = pwd.getpwuid(os.geteuid())
    ent_gr = grp.getgrgid(ent_pw.pw_gid)

    cfg: dict[str, dict[str, str]] = {
        "daemon": {
            "seat": wldm.policy.DEFAULT_SEAT,
            "socket-path": "/run/wldm/greeter.sock",
            "log-path": "",
            "poweroff-command": "systemctl poweroff",
            "reboot-command": "systemctl reboot",
            "suspend-command": "",
            "hibernate-command": "",
        },
        "greeter": {
            "user": ent_pw.pw_name,
            "group": ent_gr.gr_name,
            "tty": "7",
            "theme": "default",
            "session-dirs": ":".join(wldm.policy.SYSTEM_WAYLAND_SESSION_DIRS),
            "user-session-dir": wldm.policy.USER_WAYLAND_SESSION_DIR,
            "command": "cage -s -m last --",
            "pam-service": "system-login",
            "max-restarts": "3",
            "user-sessions": "yes",
            "log-path": "",
        },
        "session": {
            "pam-service": "login",
            "command": "/usr/share/wldm/scripts/wayland-session",
            "pre-command": "",
            "post-command": "",
        },
        "keyboard": {
            "rules": "",
            "model": "",
            "layout": "",
            "variant": "",
            "options": "",
        },
    }

    allowed = {
        "daemon": set(cfg["daemon"]),
        "greeter": set(cfg["greeter"]),
        "session": set(cfg["session"]),
        "keyboard": set(cfg["keyboard"]),
    }

    for path in _config_candidates():
        try:
            parsed = wldm.inifile.read_ini_file(
                path,
                allowed=allowed,
                max_size=wldm.policy.CONFIG_MAX_FILE_SIZE,
            )
            if parsed.get_str("session", "command") != "":
                parsed.sections["session"]["command"] = _resolve_command_path(
                    parsed.get_str("session", "command"),
                    os.path.dirname(path),
                )
            for section, values in parsed.sections.items():
                cfg[section].update(values)
            return wldm.inifile.IniFile(cfg)
        except FileNotFoundError:
            continue
        except (OSError, RuntimeError, OverflowError, UnicodeError, ValueError) as e:
            wldm.logger.warning("ignoring invalid config file %s: %s", path, e)

    return wldm.inifile.IniFile(cfg)
