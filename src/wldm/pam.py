#!/usr/bin/env python
# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026  Alexey Gladkov <legion@kernel.org>

from typing import Dict, List, Any

import ctypes
from ctypes import POINTER, byref, c_char, c_char_p, c_void_p, cast, sizeof

import wldm
import wldm.config
import wldm._pam_ffi as ffi
from wldm._libc import calloc, free

logger = wldm.logger

# Compatibility exports for callers and tests that use wldm.pam as the
# higher-level API surface.
PAM_SUCCESS = ffi.PAM_SUCCESS
PAM_CONV_ERR = ffi.PAM_CONV_ERR
PAM_PROMPT_ECHO_OFF = ffi.PAM_PROMPT_ECHO_OFF
PAM_TTY = ffi.PAM_TTY

PamMessage = ffi.PamMessage
PamResponse = ffi.PamResponse
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


def _password_conv(n_messages: int, messages: List[ffi.PamMessage],
                   response: Any,
                   appdata_ptr: Any) -> int:
    if not appdata_ptr:
        return ffi.PAM_CONV_ERR

    resp_ptr = calloc(n_messages, sizeof(ffi.PamResponse))
    if not resp_ptr:
        return ffi.PAM_CONV_ERR

    password = ctypes.cast(appdata_ptr, c_char_p).value
    if not password:
        free(resp_ptr)
        return ffi.PAM_CONV_ERR

    arr = cast(resp_ptr, POINTER(ffi.PamResponse))

    for i in range(n_messages):
        if messages[i].contents.msg_style == ffi.PAM_PROMPT_ECHO_OFF:
            resp = calloc(len(password) + 1, sizeof(c_char))
            if not resp:
                for j in range(i):
                    if arr[j].resp:
                        free(cast(arr[j].resp, c_void_p))
                free(resp_ptr)
                return PAM_CONV_ERR
            ctypes.memmove(resp, c_char_p(password), len(password))
            arr[i].resp = resp
            arr[i].resp_retcode = 0

    response[0] = arr

    return ffi.PAM_SUCCESS


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


def authenticate(username: bytes, password: bytes) -> bool:
    service: bytes = b"login"

    pw_ptr = ctypes.c_char_p(password)
    conv = ffi.PamConv(password_conv, ctypes.cast(pw_ptr, c_void_p))
    pamh = ffi.pam_handle_t()

    rc = libpam.pam_start(service, username, byref(conv), byref(pamh))

    if rc != ffi.PAM_SUCCESS:
        err = pam_error_str(None, rc)
        raise RuntimeError(f"pam_start failed: {rc} ({err})")

    try:
        rc = libpam.pam_authenticate(pamh, 0)
    finally:
        end_pam(pamh)

    if rc != ffi.PAM_SUCCESS:
        return False

    return True
