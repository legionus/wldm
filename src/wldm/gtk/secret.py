# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026  Alexey Gladkov <legion@kernel.org>

import ctypes
from ctypes import create_string_buffer
from typing import Any

import wldm.gtk._ffi as ffi
from wldm.libc.memory import strlen
from wldm.secret import SecretBytes


def read_password_secret(editable: Any) -> SecretBytes:
    gtk = ffi.load_gtk_library()

    if gtk is None:
        return SecretBytes(editable.get_text().encode("utf-8"))

    pointer = ffi.editable_pointer(editable)
    if pointer is None or pointer.value is None:
        return SecretBytes(editable.get_text().encode("utf-8"))

    text_ptr = gtk.gtk_editable_get_text(pointer)
    if text_ptr is None:
        return SecretBytes()

    length = int(strlen(text_ptr))
    buffer = create_string_buffer(length + 1)
    ctypes.memmove(buffer, text_ptr, length + 1)

    return SecretBytes.from_buffer(buffer, length)
