#!/usr/bin/env python
# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026  Alexey Gladkov <legion@kernel.org>

import os

import wldm
import wldm.libc.wtmp

logger = wldm.logger


def login(tty_path: str, username: str, host: str = "") -> None:
    if wldm.libc.wtmp.logwtmp(os.path.basename(tty_path).encode(), username.encode(), host.encode()) is False:
        logger.debug("wtmp support is not available")


def logout(tty_path: str, host: str = "") -> None:
    if wldm.libc.wtmp.logwtmp(os.path.basename(tty_path).encode(), b"", host.encode()) is False:
        logger.debug("wtmp support is not available")
