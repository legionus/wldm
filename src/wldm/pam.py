#!/usr/bin/env python
# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026  Alexey Gladkov <legion@kernel.org>

from typing import Dict, List, Any

import ctypes
from ctypes import POINTER, Structure, c_char, c_char_p, c_int, c_void_p, c_size_t
from ctypes import sizeof, byref, cast
from ctypes.util import find_library

import wldm
import wldm.config

logger = wldm.logger

# --- PAM constants (small subset) ---
PAM_SUCCESS = 0
PAM_CONV_ERR = 19
PAM_PROMPT_ECHO_OFF = 1
PAM_ESTABLISH_CRED = 2
PAM_DELETE_CRED = 4
PAM_TTY = 3

# Load libraries
libpam = ctypes.CDLL(find_library("pam"))
libc = ctypes.CDLL(find_library("c"))


# Definitions for pam structures
class PamMessage(Structure):
    _fields_ = [("msg_style", c_int),
                ("msg", c_char_p)]


class PamResponse(Structure):
    _fields_ = [("resp", c_char_p),
                ("resp_retcode", c_int)]


# conv function type
# int conv(int num_msg, const struct pam_message **msg,
#          struct pam_response **resp, void *appdata_ptr);
PAM_CONV_FUNC = ctypes.CFUNCTYPE(c_int, c_int, POINTER(POINTER(PamMessage)),
                                 POINTER(POINTER(PamResponse)), c_void_p)


class PamConv(Structure):
    _fields_ = [("conv", PAM_CONV_FUNC),
                ("appdata_ptr", c_void_p)]


# pam_handle_t is an opaque pointer
pam_handle_t = c_void_p

# Declare functions we'll use
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

# Some libpam versions don't include this function
if hasattr(libpam, 'pam_end'):
    libpam.pam_end.argtypes = [pam_handle_t, c_int]
    libpam.pam_end.restype = c_int

libc.calloc.argtypes = [c_size_t, c_size_t]
libc.calloc.restype = c_void_p
libc.free.argtypes = [c_void_p]
libc.free.restype = None


# Simple conv that returns no responses (suitable if PAM doesn't ask for input)
# pylint: disable-next=unused-argument
def _simple_conv(n_messages: int, messages: List[PamMessage], response: Any, appdata_ptr: Any) -> int:
    # If PAM was to ask for a password or beep, this conv does not provide
    # input. We return PAM_SUCCESS but do not allocate responses.
    # If you need to handle password prompts, allocate PamResponse array and
    # fill resp.
    return PAM_SUCCESS


def _password_conv(n_messages: int, messages: List[PamMessage],
                   response: Any,
                   appdata_ptr: Any) -> int:
    if not appdata_ptr:
        return PAM_CONV_ERR

    resp_ptr = libc.calloc(n_messages, sizeof(PamResponse))
    if not resp_ptr:
        return PAM_CONV_ERR

    password = ctypes.cast(appdata_ptr, c_char_p).value
    if not password:
        libc.free(resp_ptr)
        return PAM_CONV_ERR

    arr = cast(resp_ptr, POINTER(PamResponse))

    for i in range(n_messages):
        if messages[i].contents.msg_style == PAM_PROMPT_ECHO_OFF:
            resp = libc.calloc(len(password) + 1, sizeof(c_char))
            if not resp:
                for j in range(i):
                    if arr[j].resp:
                        libc.free(cast(arr[j].resp, c_void_p))
                libc.free(resp_ptr)
                return PAM_CONV_ERR
            ctypes.memmove(resp, c_char_p(password), len(password))
            arr[i].resp = resp
            arr[i].resp_retcode = 0

    response[0] = arr

    return PAM_SUCCESS


simple_conv = PAM_CONV_FUNC(_simple_conv)
password_conv = PAM_CONV_FUNC(_password_conv)


def pam_error_str(pamh: Any, code: int) -> str:
    try:
        msg = libpam.pam_strerror(pamh, code)
        if msg is None:
            return f"pam error {code}"
        return str(msg.decode())
    except Exception:
        return f"PAM error code {code}"


def start_pam(service: str, user: str) -> Any:
    conv = PamConv(simple_conv, None)
    pamh = pam_handle_t()

    rc = libpam.pam_start(service.encode(), user.encode(), byref(conv),
                          byref(pamh))
    if rc != PAM_SUCCESS:
        err = pam_error_str(None, rc)
        raise RuntimeError(f"pam_start failed: {rc} ({err})")

    return pamh


def open_pam_session(pamh: Any) -> None:
    # check account
    rc = libpam.pam_acct_mgmt(pamh, 0)
    if rc != PAM_SUCCESS:
        err = pam_error_str(pamh, rc)
        raise RuntimeError(f"pam_acct_mgmt failed: {rc} ({err})")

    rc = libpam.pam_setcred(pamh, PAM_ESTABLISH_CRED)
    if rc != PAM_SUCCESS:
        err = pam_error_str(pamh, rc)
        raise RuntimeError(f"pam_setcred: {rc} ({err})")

    rc = libpam.pam_open_session(pamh, 0)
    if rc != PAM_SUCCESS:
        err = pam_error_str(pamh, rc)
        raise RuntimeError(f"pam_open_session failed: {rc} ({err})")


def open_pam_session_only(pamh: Any) -> None:
    rc = libpam.pam_open_session(pamh, 0)
    if rc != PAM_SUCCESS:
        err = pam_error_str(pamh, rc)
        raise RuntimeError(f"pam_open_session failed: {rc} ({err})")


def set_pam_item(pamh: Any, item_type: int, value: str) -> None:
    rc = libpam.pam_set_item(pamh, item_type, ctypes.cast(c_char_p(value.encode()), c_void_p))
    if rc != PAM_SUCCESS:
        err = pam_error_str(pamh, rc)
        raise RuntimeError(f"pam_set_item failed: {rc} ({err})")


def putenv(pamh: Any, name: str, value: str) -> None:
    entry = f"{name}={value}".encode()
    rc = libpam.pam_putenv(pamh, c_char_p(entry))
    if rc != PAM_SUCCESS:
        err = pam_error_str(pamh, rc)
        raise RuntimeError(f"pam_putenv failed: {rc} ({err})")


def close_pam_session(pamh: Any) -> None:
    rc = libpam.pam_setcred(pamh, PAM_DELETE_CRED)
    if rc != PAM_SUCCESS:
        err = pam_error_str(pamh, rc)
        raise RuntimeError(f"pam_setcred: {rc} ({err})")

    rc = libpam.pam_close_session(pamh, 0)
    if rc != PAM_SUCCESS:
        err = pam_error_str(pamh, rc)
        raise RuntimeError(f"pam_close_session failed: {rc} ({err})")


def end_pam(pamh: Any) -> None:
    if hasattr(libpam, 'pam_end'):
        # second arg is status; pass PAM_SUCCESS for normal ending
        libpam.pam_end(pamh, PAM_SUCCESS)


def getenvlist(pamh: Any, encoding: str = 'utf-8') -> Dict[str, str]:
    env_list = libpam.pam_getenvlist(pamh)

    env_count = 0
    pam_env_items = {}

    while True:
        try:
            item = env_list[env_count]
        except IndexError:
            break

        if not item:
            # end of the list
            break

        env_item = item.decode(encoding)

        try:
            pam_key, pam_value = env_item.split("=", 1)
        except ValueError:
            # Incorrectly formatted envlist item
            pass
        else:
            pam_env_items[pam_key] = pam_value

        env_count += 1

    return pam_env_items


def authenticate(username: bytes, password: bytes) -> bool:
    service: bytes = b"login"

    pw_ptr = ctypes.c_char_p(password)

    conv = PamConv(password_conv, ctypes.cast(pw_ptr, c_void_p))
    pamh = pam_handle_t()

    rc = libpam.pam_start(service, username, byref(conv), byref(pamh))

    if rc != PAM_SUCCESS:
        err = pam_error_str(None, rc)
        raise RuntimeError(f"pam_start failed: {rc} ({err})")

    try:
        rc = libpam.pam_authenticate(pamh, 0)
    finally:
        end_pam(pamh)

    if rc != PAM_SUCCESS:
        return False

    return True
