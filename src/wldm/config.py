#!/usr/bin/env python
# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026  Alexey Gladkov <legion@kernel.org>

import os
import os.path
import pwd
import grp

import wldm
import wldm.inifile
import wldm.policy


def _config_candidates() -> list[str]:
    candidates: list[str] = []

    if "WLDM_CONFIG" in os.environ:
        candidates.append(os.environ["WLDM_CONFIG"])

    candidates.append("/etc/wldm.ini")

    return candidates


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
            "data-dir": "",
            "locale-dir": "",
            "state-dir": "",
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
            "execute": "",
            "pre-execute": "",
            "post-execute": "",
        },
        "dbus": {
            "enabled": "no",
            "user": ent_pw.pw_name,
            "service": "org.freedesktop.DisplayManager",
            "log-path": "",
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
        "dbus": set(cfg["dbus"]),
        "keyboard": set(cfg["keyboard"]),
    }

    source_tree = os.environ.get("WLDM_SOURCE_TREE", "").strip()

    for path in _config_candidates():
        try:
            parsed = wldm.inifile.read_ini_file(path, allowed=allowed,
                                                max_size=wldm.policy.CONFIG_MAX_FILE_SIZE)

            if source_tree:
                if parsed.get_str("greeter", "data-dir") != "":
                    parsed.sections["greeter"]["data-dir"] = wldm.resolve_config_path(
                            parsed.get_str("greeter", "data-dir"),
                            base_dir=source_tree)

                if parsed.get_str("greeter", "locale-dir") != "":
                    parsed.sections["greeter"]["locale-dir"] = wldm.resolve_config_path(
                            parsed.get_str("greeter", "locale-dir"),
                            base_dir=source_tree)

                if parsed.get_str("greeter", "state-dir") != "":
                    parsed.sections["greeter"]["state-dir"] = wldm.resolve_config_path(
                            parsed.get_str("greeter", "state-dir"),
                            base_dir=source_tree)

                for key in ["execute", "pre-execute", "post-execute"]:
                    if parsed.get_str("session", key) == "":
                        continue

                    parsed.sections["session"][key] = wldm.resolve_config_path(
                            parsed.get_str("session", key),
                            base_dir=source_tree)

            for section, values in parsed.sections.items():
                cfg[section].update(values)

            return wldm.inifile.IniFile(cfg)

        except FileNotFoundError:
            continue

        except (OSError, RuntimeError, OverflowError, UnicodeError, ValueError) as e:
            wldm.logger.warning("ignoring invalid config file %s: %s", path, e)

    return wldm.inifile.IniFile(cfg)
