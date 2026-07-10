# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026  Alexey Gladkov <legion@kernel.org>

import ctypes
from ctypes import POINTER, Structure, c_char_p, c_int, c_void_p
from ctypes.util import find_library
from typing import Any

c_char = ctypes.c_char
_libpam: Any | None = None


def _require_library(name: str) -> str:
    path = find_library(name)
    if path is None:
        raise RuntimeError(f"unable to locate required library: {name}")
    return path


class PamMessage(Structure):
    _fields_ = [("msg_style", c_int),
                ("msg", c_char_p)]


class PamResponse(Structure):
    _fields_ = [("resp", c_char_p),
                ("resp_retcode", c_int)]


PAM_CONV_FUNC = ctypes.CFUNCTYPE(c_int, c_int, POINTER(POINTER(PamMessage)),
                                 POINTER(POINTER(PamResponse)), c_void_p)


class PamConv(Structure):
    _fields_ = [("conv", PAM_CONV_FUNC),
                ("appdata_ptr", c_void_p)]


pam_handle_t = c_void_p


def _configure_libpam(libpam: Any) -> Any:
    libpam.pam_start.argtypes = [c_char_p, c_char_p, POINTER(PamConv),
                                 POINTER(pam_handle_t)]
    libpam.pam_start.restype = c_int

    libpam.pam_open_session.argtypes = [pam_handle_t, c_int]
    libpam.pam_open_session.restype = c_int

    libpam.pam_close_session.argtypes = [pam_handle_t, c_int]
    libpam.pam_close_session.restype = c_int

    libpam.pam_strerror.argtypes = [pam_handle_t, c_int]
    libpam.pam_strerror.restype = c_char_p

    libpam.pam_getenvlist.argtypes = [pam_handle_t]
    libpam.pam_getenvlist.restype = POINTER(c_char_p)

    libpam.pam_putenv.argtypes = [pam_handle_t, c_char_p]
    libpam.pam_putenv.restype = c_int

    libpam.pam_acct_mgmt.argtypes = [pam_handle_t, c_int]
    libpam.pam_acct_mgmt.restype = c_int

    libpam.pam_setcred.argtypes = [pam_handle_t, c_int]
    libpam.pam_setcred.restype = c_int

    libpam.pam_authenticate.argtypes = [pam_handle_t, c_int]
    libpam.pam_authenticate.restype = c_int

    libpam.pam_set_item.argtypes = [pam_handle_t, c_int, c_void_p]
    libpam.pam_set_item.restype = c_int

    if hasattr(libpam, 'pam_end'):
        libpam.pam_end.argtypes = [pam_handle_t, c_int]
        libpam.pam_end.restype = c_int

    return libpam


def libpam() -> Any:
    """Return the process-local libpam binding, loading it on first use."""
    global _libpam

    if _libpam is None:
        _libpam = _configure_libpam(ctypes.CDLL(_require_library("pam")))

    return _libpam
