#!/usr/bin/env python
# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026  Alexey Gladkov <legion@kernel.org>

import configparser
import os
import os.path
import sys
import pwd
import grp

import wldm.policy


def _config_candidates() -> list[str]:
    candidates: list[str] = []

    if "WLDM_CONFIG" in os.environ:
        candidates.append(os.environ["WLDM_CONFIG"])

    if "WLDM_PROGNAME" in os.environ:
        script_top = os.path.dirname(os.path.abspath(os.environ["WLDM_PROGNAME"]))
        candidates.append(os.path.join(script_top, "config", "wldm.ini"))

    candidates.append(os.path.join(sys.prefix, "share", "wldm", "config", "wldm.ini"))
    candidates.append("/etc/wldm.ini")

    return candidates


def read_config() -> configparser.ConfigParser:
    cfg = configparser.ConfigParser()

    ent_pw = pwd.getpwuid(os.geteuid())
    ent_gr = grp.getgrgid(ent_pw.pw_gid)

    cfg["daemon"] = {
            "seat": wldm.policy.DEFAULT_SEAT,
            "socket-path": "/run/wldm/greeter.sock",
            "log-path": "",
            "poweroff-command": "systemctl poweroff",
            "reboot-command": "systemctl reboot",
            "suspend-command": "",
            "hibernate-command": "",
            }

    cfg["greeter"] = {
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
            }

    cfg["session"] = {
            "pam-service": "login",
            "command": "default",
            "pre-command": "",
            "post-command": "",
            }

    for path in _config_candidates():
        if cfg.read([path]):
            return cfg

    return cfg
