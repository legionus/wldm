#!/usr/bin/env python
# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026  Alexey Gladkov <legion@kernel.org>

import ctypes
from ctypes import c_char_p, c_void_p, create_string_buffer
from ctypes.util import find_library
from typing import Any

from wldm._libc import strlen
from wldm.secret import SecretBytes


_gtk: ctypes.CDLL | None = None


def _load_library(name: str) -> ctypes.CDLL | None:
    path = find_library(name)
    if path is None:
        return None
    return ctypes.CDLL(path)


def _load_gtk_library() -> ctypes.CDLL | None:
    """Load libgtk lazily for the native password-entry fast path.

    Returns:
        The loaded `ctypes.CDLL` object for `gtk-4`, or `None` when the native
        helper is unavailable.
    """
    global _gtk

    if _gtk is not None:
        return _gtk

    gtk = _load_library("gtk-4")

    if gtk is not None:
        gtk.gtk_editable_get_text.argtypes = [c_void_p]
        gtk.gtk_editable_get_text.restype = c_char_p

    _gtk = gtk
    return _gtk

_pycapsule_get_pointer = ctypes.pythonapi.PyCapsule_GetPointer
_pycapsule_get_pointer.argtypes = [ctypes.py_object, c_char_p]
_pycapsule_get_pointer.restype = c_void_p


def _editable_pointer(editable: Any) -> c_void_p | None:
    pointer = getattr(editable, "__gpointer__", None)

    if pointer is None:
        return None

    if isinstance(pointer, int):
        return c_void_p(pointer)

    try:
        return c_void_p(_pycapsule_get_pointer(pointer, None))
    except Exception:
        return None


def read_password_secret(editable: Any) -> SecretBytes:
    gtk = _load_gtk_library()

    if gtk is None:
        return SecretBytes(editable.get_text().encode("utf-8"))

    pointer = _editable_pointer(editable)
    if pointer is None or pointer.value is None:
        return SecretBytes(editable.get_text().encode("utf-8"))

    text_ptr = gtk.gtk_editable_get_text(pointer)
    if text_ptr is None:
        return SecretBytes()

    length = int(strlen(text_ptr))
    buffer = create_string_buffer(length + 1)
    ctypes.memmove(buffer, text_ptr, length + 1)

    return SecretBytes.from_buffer(buffer, length)
