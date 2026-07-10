# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026  Alexey Gladkov <legion@kernel.org>

# pylint: disable=wrong-import-position

# --- PAM constants (small subset) ---
PAM_SUCCESS = 0
PAM_AUTH_ERR = 7
PAM_CRED_INSUFFICIENT = 8
PAM_USER_UNKNOWN = 10
PAM_MAXTRIES = 11
PAM_NEW_AUTHTOK_REQD = 12
PAM_ACCT_EXPIRED = 13
PAM_CONV_ERR = 19
PAM_AUTHTOK_LOCK_BUSY = 22
PAM_AUTHTOK_DISABLE_AGING = 23
PAM_ABORT = 26
PAM_PROMPT_ECHO_OFF = 1
PAM_PROMPT_ECHO_ON = 2
PAM_ERROR_MSG = 3
PAM_TEXT_INFO = 4
PAM_ESTABLISH_CRED = 2
PAM_DELETE_CRED = 4
PAM_TTY = 3

from wldm.pam._ffi import PAM_CONV_FUNC, PamConv, PamMessage, PamResponse, c_char, pam_handle_t
from wldm.pam.funcs import (
    close_pam_session,
    end_pam,
    getenvlist,
    open_pam_session,
    open_pam_session_only,
    pam_error_str,
    putenv,
    set_pam_item,
    start_pam,
)

__all__ = [
    "PAM_ABORT",
    "PAM_ACCT_EXPIRED",
    "PAM_AUTH_ERR",
    "PAM_AUTHTOK_DISABLE_AGING",
    "PAM_AUTHTOK_LOCK_BUSY",
    "PAM_CONV_ERR",
    "PAM_CONV_FUNC",
    "PAM_CRED_INSUFFICIENT",
    "PAM_DELETE_CRED",
    "PAM_ERROR_MSG",
    "PAM_ESTABLISH_CRED",
    "PAM_MAXTRIES",
    "PAM_NEW_AUTHTOK_REQD",
    "PAM_PROMPT_ECHO_OFF",
    "PAM_PROMPT_ECHO_ON",
    "PAM_SUCCESS",
    "PAM_TEXT_INFO",
    "PAM_TTY",
    "PAM_USER_UNKNOWN",
    "PamConv",
    "PamMessage",
    "PamResponse",
    "c_char",
    "close_pam_session",
    "end_pam",
    "getenvlist",
    "open_pam_session",
    "open_pam_session_only",
    "pam_error_str",
    "pam_handle_t",
    "putenv",
    "set_pam_item",
    "start_pam",
]
