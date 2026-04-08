#!/usr/bin/env python
# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026  Alexey Gladkov <legion@kernel.org>

import dataclasses
import os

import gi  # type: ignore[import-untyped]

gi.require_version("Gtk", "4.0")

# pylint: disable-next=wrong-import-position
from gi.repository import Gdk  # type: ignore[import-untyped]

# pylint: disable-next=wrong-import-position
import wldm

logger = wldm.logger


@dataclasses.dataclass(frozen=True)
class KeyboardLayout:
    """One configured keyboard layout and its short display name."""

    short_name: str
    long_name: str


def _configured_keyboard_short_names() -> list[str]:
    """Return configured short XKB layout names from the environment."""
    value = os.environ.get("XKB_DEFAULT_LAYOUT", "").strip()
    return [item.strip() for item in value.split(",") if item.strip()]


def keyboard_state() -> tuple[list[KeyboardLayout], int]:
    """Read available keyboard layouts and the active layout index from GTK."""
    display = Gdk.Display.get_default()

    if display is None or not hasattr(display, "get_default_seat"):
        return [], -1

    seat = display.get_default_seat()
    if seat is None or not hasattr(seat, "get_keyboard"):
        return [], -1

    keyboard = seat.get_keyboard()
    if keyboard is None:
        return [], -1

    if not hasattr(keyboard, "get_layout_names") or not hasattr(keyboard, "get_active_layout_index"):
        return [], -1

    try:
        layout_names = keyboard.get_layout_names()
        active_index = keyboard.get_active_layout_index()
    except Exception as e:
        logger.debug("unable to read keyboard layout state: %s", e)
        return [], -1

    if not layout_names or not isinstance(active_index, int):
        return [], -1

    if active_index < 0 or active_index >= len(layout_names):
        return [], -1

    configured_names = _configured_keyboard_short_names()
    layouts = []

    for index, name in enumerate(layout_names):
        long_name = str(name).strip()

        if not long_name:
            continue

        short_name = configured_names[index] if index < len(configured_names) else long_name
        layouts.append(KeyboardLayout(short_name=short_name, long_name=long_name))

    if active_index >= len(layouts):
        return [], -1

    return layouts, active_index
