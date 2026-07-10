# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026  Alexey Gladkov <legion@kernel.org>

import ctypes
from ctypes import c_char_p, c_void_p
from ctypes.util import find_library
from typing import Any

import wldm

_gtk: ctypes.CDLL | None = None
logger = wldm.logger


def load_library(name: str) -> ctypes.CDLL | None:
    path = find_library(name)
    if path is None:
        return None
    return ctypes.CDLL(path)


def load_gtk_library() -> ctypes.CDLL | None:
    """Load libgtk lazily for the native password-entry fast path.

    Returns:
        The loaded `ctypes.CDLL` object for `gtk-4`, or `None` when the native
        helper is unavailable.
    """
    global _gtk

    if _gtk is not None:
        return _gtk

    gtk = load_library("gtk-4")

    if gtk is not None:
        gtk.gtk_editable_get_text.argtypes = [c_void_p]
        gtk.gtk_editable_get_text.restype = c_char_p

    _gtk = gtk
    return _gtk


_pycapsule_get_pointer = ctypes.pythonapi.PyCapsule_GetPointer
_pycapsule_get_pointer.argtypes = [ctypes.py_object, c_char_p]
_pycapsule_get_pointer.restype = c_void_p


def editable_pointer(editable: Any) -> c_void_p | None:
    pointer = getattr(editable, "__gpointer__", None)

    if pointer is None:
        return None

    if isinstance(pointer, int):
        return c_void_p(pointer)

    try:
        return c_void_p(_pycapsule_get_pointer(pointer, None))
    except Exception as e:
        logger.debug("unable to extract Gtk editable pointer, using text fallback: %s", e)
        return None
