#!/usr/bin/env python
# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026  Alexey Gladkov <legion@kernel.org>

import os
import fcntl
import array

from typing import Optional

import wldm

logger = wldm.logger

MIN_NR_CONSOLES = 1   # must be at least 1
MAX_NR_CONSOLES = 63  # serial lines start at 64

VT_OPENQRY     = 0x5600  # find available vt
VT_ACTIVATE    = 0x5606  # make vt active
VT_WAITACTIVE  = 0x5607  # wait for vt active
VT_DISALLOCATE = 0x5608  # free memory associated to vt

TIOCSCTTY = 0x540E  # make the given terminal the controlling terminal.

def open_console() -> Optional[int]:
    dev_candidates = [
            '/dev/tty0',
            '/dev/systty',
            '/dev/console']
    errors = []

    for dev in dev_candidates:
        try:
            console = os.open(dev, os.O_RDONLY)
        except OSError as e:
            errors.append(f"{dev}: {e}")
            continue

        logger.debug("tty device: %s", dev)
        return console

    if errors:
        logger.critical("unable to open console from %s", ", ".join(errors))
    return None


def device_name(num: int) -> str:
    return f"/dev/tty{num}"


def available(console: int) -> Optional[int]:
    try:
        buf = array.array('i', [0])

        fcntl.ioctl(console, VT_OPENQRY, buf, True)
        num = int(buf[0])

        if num >= MIN_NR_CONSOLES and num <= MAX_NR_CONSOLES:
            return num

    except OSError as e:
        logger.critical("unable to get available tty: %r", e)

    return None


def change(console: int, num: int) -> bool:
    if num < MIN_NR_CONSOLES or num > MAX_NR_CONSOLES:
        return False
    try:
        fcntl.ioctl(console, VT_ACTIVATE, num, True)
        fcntl.ioctl(console, VT_WAITACTIVE, num, True)
        return True

    except OSError as e:
        logger.critical("unable to change tty: %r", e)

    return False


def dealloc(console: int, num: int) -> bool:
    if num < MIN_NR_CONSOLES or num > MAX_NR_CONSOLES:
        return False
    try:
        fcntl.ioctl(console, VT_DISALLOCATE, num, True)
        return True

    except OSError as e:
        logger.critical("unable to dealloc tty: %r", e)

    return False


def make_control_tty(console: int) -> bool:
    try:
        fcntl.ioctl(console, TIOCSCTTY, 0, True)
        return True

    except OSError as e:
        logger.critical("unable to set controlling the terminal: %r", e)

    return False


class TTYdevice:
    def __init__(self, console: int, uid: int, number: Optional[int] = None):
        self.console = console
        self.uid = uid

        self.number = number if number is not None else available(self.console)

        if self.number is None:
            raise RuntimeError("No terminal available")

        self.filename = device_name(self.number)

        self.fd = os.open(self.filename, os.O_RDWR)
        os.fchown(self.fd, self.uid, -1)

    def close(self) -> None:
        os.fchown(self.fd, 0, -1)
        os.close(self.fd)

    def switch(self) -> None:
        if self.number is not None:
            change(self.console, self.number)
