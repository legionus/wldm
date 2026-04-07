#!/usr/bin/env python
# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026  Alexey Gladkov <legion@kernel.org>

from typing import Dict, List, Any

import ctypes
from ctypes import byref, c_char_p, c_void_p

import wldm
import wldm._pam_ffi as ffi

logger = wldm.logger

# Compatibility exports for callers and tests that use wldm.pam as the
# higher-level API surface.
PAM_SUCCESS = ffi.PAM_SUCCESS
PAM_CONV_ERR = ffi.PAM_CONV_ERR
PAM_TTY = ffi.PAM_TTY

PamMessage = ffi.PamMessage
PamConv = ffi.PamConv
PAM_CONV_FUNC = ffi.PAM_CONV_FUNC
libpam = ffi.libpam


# Simple conv that returns no responses (suitable if PAM doesn't ask for input)
# pylint: disable-next=unused-argument
def _simple_conv(n_messages: int, messages: List[ffi.PamMessage], response: Any, appdata_ptr: Any) -> int:
    # If PAM was to ask for a password or beep, this conv does not provide
    # input. We return PAM_SUCCESS but do not allocate responses.
    # If you need to handle password prompts, allocate PamResponse array and
    # fill resp.
    return ffi.PAM_SUCCESS

simple_conv = PAM_CONV_FUNC(_simple_conv)


def pam_error_str(pamh: Any, code: int) -> str:
    try:
        msg = libpam.pam_strerror(pamh, code)
        if msg is None:
            return f"pam error {code}"

        return str(msg.decode())

    except Exception:
        return f"PAM error code {code}"


def start_pam(service: str, user: str) -> Any:
    conv = ffi.PamConv(simple_conv, None)
    pamh = ffi.pam_handle_t()

    rc = libpam.pam_start(service.encode(), user.encode(), byref(conv),
                          byref(pamh))

    if rc != ffi.PAM_SUCCESS:
        err = pam_error_str(None, rc)
        raise RuntimeError(f"pam_start failed: {rc} ({err})")

    return pamh


def open_pam_session(pamh: Any) -> None:
    # check account
    rc = libpam.pam_acct_mgmt(pamh, 0)

    if rc != ffi.PAM_SUCCESS:
        err = pam_error_str(pamh, rc)
        raise RuntimeError(f"pam_acct_mgmt failed: {rc} ({err})")

    rc = libpam.pam_setcred(pamh, ffi.PAM_ESTABLISH_CRED)

    if rc != ffi.PAM_SUCCESS:
        err = pam_error_str(pamh, rc)
        raise RuntimeError(f"pam_setcred: {rc} ({err})")

    rc = libpam.pam_open_session(pamh, 0)

    if rc != ffi.PAM_SUCCESS:
        err = pam_error_str(pamh, rc)
        raise RuntimeError(f"pam_open_session failed: {rc} ({err})")


def open_pam_session_only(pamh: Any) -> None:
    rc = libpam.pam_open_session(pamh, 0)

    if rc != ffi.PAM_SUCCESS:
        err = pam_error_str(pamh, rc)
        raise RuntimeError(f"pam_open_session failed: {rc} ({err})")


def set_pam_item(pamh: Any, item_type: int, value: str) -> None:
    rc = libpam.pam_set_item(pamh, item_type, ctypes.cast(c_char_p(value.encode()), c_void_p))

    if rc != ffi.PAM_SUCCESS:
        err = pam_error_str(pamh, rc)
        raise RuntimeError(f"pam_set_item failed: {rc} ({err})")


def putenv(pamh: Any, name: str, value: str) -> None:
    entry = f"{name}={value}".encode()

    rc = libpam.pam_putenv(pamh, c_char_p(entry))

    if rc != ffi.PAM_SUCCESS:
        err = pam_error_str(pamh, rc)
        raise RuntimeError(f"pam_putenv failed: {rc} ({err})")


def close_pam_session(pamh: Any) -> None:
    rc = libpam.pam_setcred(pamh, ffi.PAM_DELETE_CRED)

    if rc != ffi.PAM_SUCCESS:
        err = pam_error_str(pamh, rc)
        raise RuntimeError(f"pam_setcred: {rc} ({err})")

    rc = libpam.pam_close_session(pamh, 0)

    if rc != ffi.PAM_SUCCESS:
        err = pam_error_str(pamh, rc)
        raise RuntimeError(f"pam_close_session failed: {rc} ({err})")


def end_pam(pamh: Any) -> None:
    if hasattr(libpam, 'pam_end'):
        # second arg is status; pass PAM_SUCCESS for normal ending
        libpam.pam_end(pamh, ffi.PAM_SUCCESS)


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
