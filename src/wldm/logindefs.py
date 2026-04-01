#!/usr/bin/env python
# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026  Alexey Gladkov <legion@kernel.org>

import os
import re

from typing import Dict

import wldm
import wldm.policy

LOGIN_DEFS = "/etc/login.defs"

login_defs: Dict[str, str] = {}


def read_values() -> None:
    global login_defs
    login_defs = {}

    if not os.access(LOGIN_DEFS, os.R_OK):
        return

    try:
        with wldm.open_regular_text_file(LOGIN_DEFS, max_size=wldm.policy.LOGIN_DEFS_MAX_FILE_SIZE) as fd:
            for line in fd:
                line = line.strip()

                if len(line) == 0 or line.startswith("#"):
                    continue

                name, value = re.split(r'\s+', line, maxsplit=1)
                login_defs[name] = value

    except (OSError, RuntimeError, OverflowError, UnicodeError):
        return


def get_bool(name: str) -> bool:
    if name in login_defs:
        return login_defs[name] == "yes"
    return False


def get_number(name: str) -> int:
    try:
        if name in login_defs:
            if login_defs[name].startswith("0x"):
                return int(login_defs[name], base=16)

            if login_defs[name].startswith("0"):
                return int(login_defs[name], base=8)

            return int(login_defs[name], base=10)

    except ValueError:
        pass
    return 0


def get_string(name: str) -> str:
    return login_defs.get(name, "")
