# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026  Alexey Gladkov <legion@kernel.org>

import ctypes
from ctypes import byref, c_char_p, c_void_p
from typing import Any, Dict, List

import wldm
import wldm.pam as pam
import wldm.pam._ffi as ffi

logger = wldm.logger


# Simple conv that returns no responses (suitable if PAM doesn't ask for input)
# pylint: disable-next=unused-argument
def _simple_conv(n_messages: int, messages: List[ffi.PamMessage], response: Any, appdata_ptr: Any) -> int:
    # If PAM was to ask for a password or beep, this conv does not provide
    # input. We return PAM_SUCCESS but do not allocate responses.
    # If you need to handle password prompts, allocate PamResponse array and
    # fill resp.
    return pam.PAM_SUCCESS


simple_conv = ffi.PAM_CONV_FUNC(_simple_conv)


def pam_error_str(pamh: Any, code: int) -> str:
    try:
        msg = ffi.libpam().pam_strerror(pamh, code)
        if msg is None:
            return f"pam error {code}"

        return str(msg.decode())

    except Exception:
        return f"PAM error code {code}"


def start_pam(service: str, user: str) -> Any:
    conv = ffi.PamConv(simple_conv, None)
    pamh = ffi.pam_handle_t()

    rc = ffi.libpam().pam_start(service.encode(), user.encode(), byref(conv),
                                byref(pamh))

    if rc != pam.PAM_SUCCESS:
        err = pam_error_str(None, rc)
        raise RuntimeError(f"pam_start failed: {rc} ({err})")

    return pamh


def open_pam_session(pamh: Any) -> None:
    # check account
    rc = ffi.libpam().pam_acct_mgmt(pamh, 0)

    if rc != pam.PAM_SUCCESS:
        err = pam_error_str(pamh, rc)
        raise RuntimeError(f"pam_acct_mgmt failed: {rc} ({err})")

    rc = ffi.libpam().pam_setcred(pamh, pam.PAM_ESTABLISH_CRED)

    if rc != pam.PAM_SUCCESS:
        err = pam_error_str(pamh, rc)
        raise RuntimeError(f"pam_setcred: {rc} ({err})")

    rc = ffi.libpam().pam_open_session(pamh, 0)

    if rc != pam.PAM_SUCCESS:
        err = pam_error_str(pamh, rc)
        raise RuntimeError(f"pam_open_session failed: {rc} ({err})")


def open_pam_session_only(pamh: Any) -> None:
    rc = ffi.libpam().pam_open_session(pamh, 0)

    if rc != pam.PAM_SUCCESS:
        err = pam_error_str(pamh, rc)
        raise RuntimeError(f"pam_open_session failed: {rc} ({err})")


def set_pam_item(pamh: Any, item_type: int, value: str) -> None:
    rc = ffi.libpam().pam_set_item(pamh, item_type, ctypes.cast(c_char_p(value.encode()), c_void_p))

    if rc != pam.PAM_SUCCESS:
        err = pam_error_str(pamh, rc)
        raise RuntimeError(f"pam_set_item failed: {rc} ({err})")


def putenv(pamh: Any, name: str, value: str) -> None:
    entry = f"{name}={value}".encode()

    rc = ffi.libpam().pam_putenv(pamh, c_char_p(entry))

    if rc != pam.PAM_SUCCESS:
        err = pam_error_str(pamh, rc)
        raise RuntimeError(f"pam_putenv failed: {rc} ({err})")


def close_pam_session(pamh: Any) -> None:
    rc = ffi.libpam().pam_setcred(pamh, pam.PAM_DELETE_CRED)

    if rc != pam.PAM_SUCCESS:
        err = pam_error_str(pamh, rc)
        raise RuntimeError(f"pam_setcred: {rc} ({err})")

    rc = ffi.libpam().pam_close_session(pamh, 0)

    if rc != pam.PAM_SUCCESS:
        err = pam_error_str(pamh, rc)
        raise RuntimeError(f"pam_close_session failed: {rc} ({err})")


def end_pam(pamh: Any) -> None:
    libpam = ffi.libpam()

    if hasattr(libpam, 'pam_end'):
        # second arg is status; pass PAM_SUCCESS for normal ending
        libpam.pam_end(pamh, pam.PAM_SUCCESS)


def getenvlist(pamh: Any, encoding: str = 'utf-8') -> Dict[str, str]:
    env_list = ffi.libpam().pam_getenvlist(pamh)

    env_count = 0
    pam_env_items = {}

    while True:
        try:
            item = env_list[env_count]
        except IndexError:
            break

        if item is None:
            break

        entry = item.decode(encoding)
        name, _, value = entry.partition("=")
        if name and value:
            pam_env_items[name] = value

        env_count += 1

    return pam_env_items
