# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026  Alexey Gladkov <legion@kernel.org>

import ctypes

import wldm.pam
import wldm._pam_ffi
import wldm.secret


def test_simple_conv_returns_success():
    assert wldm.pam._simple_conv(0, [], None, None) == wldm.pam.PAM_SUCCESS


def test_pam_ffi_require_library_raises_when_missing(monkeypatch):
    monkeypatch.setattr(wldm._pam_ffi, "find_library", lambda name: None)

    try:
        wldm._pam_ffi._require_library("pam")
    except RuntimeError as exc:
        assert "required library: pam" in str(exc)
    else:
        raise AssertionError("_require_library() should fail when libpam is missing")


def test_password_conv_rejects_missing_inputs():
    response = [None]

    assert wldm.pam._password_conv(1, [], response, None) == wldm.pam.PAM_CONV_ERR


def test_password_conv_rejects_failed_alloc(monkeypatch):
    response = [None]

    monkeypatch.setattr(wldm.pam, "calloc", lambda count, size: 0)

    assert wldm.pam._password_conv(1, [], response, ctypes.c_void_p(1)) == wldm.pam.PAM_CONV_ERR


def test_password_conv_frees_response_array_when_password_is_missing(monkeypatch):
    response = [None]
    calls = []

    monkeypatch.setattr(wldm.pam, "calloc", lambda count, size: 1234)
    monkeypatch.setattr(wldm.pam, "free", lambda ptr: calls.append(ptr))
    real_cast = wldm.pam.ctypes.cast
    monkeypatch.setattr(
        wldm.pam.ctypes,
        "cast",
        lambda value, target: ctypes.c_char_p()
        if target is wldm.pam.c_char_p and value == 1
        else real_cast(value, target),
    )

    assert wldm.pam._password_conv(1, [], response, 1) == wldm.pam.PAM_CONV_ERR
    assert calls == [1234]


def test_password_conv_populates_response_for_password_prompt(monkeypatch):
    class FakeMessage:
        def __init__(self, style):
            self.contents = ctypes.Structure.__new__(wldm.pam.PamMessage)
            self.contents.msg_style = style

    allocations = [ctypes.create_string_buffer(ctypes.sizeof(wldm.pam.PamResponse) * 2),
                   ctypes.create_string_buffer(16)]

    def fake_calloc(count, size):
        return ctypes.addressof(allocations.pop(0))

    monkeypatch.setattr(wldm.pam, "calloc", fake_calloc)

    response = [None]
    password = ctypes.c_char_p(b"secret")
    messages = [FakeMessage(wldm.pam.PAM_PROMPT_ECHO_OFF), FakeMessage(999)]

    rc = wldm.pam._password_conv(2, messages, response, ctypes.cast(password, ctypes.c_void_p))

    assert rc == wldm.pam.PAM_SUCCESS
    assert response[0] is not None
def test_authenticate_accepts_secret_bytes(monkeypatch):
    calls = []

    monkeypatch.setattr(wldm.pam.libpam, "pam_start", lambda service, user, conv, pamh: 0)
    monkeypatch.setattr(wldm.pam.libpam, "pam_authenticate", lambda pamh, flags: 0)
    monkeypatch.setattr(wldm.pam, "end_pam", lambda pamh: calls.append("end"))

    secret = wldm.secret.SecretBytes(b"secret")

    assert wldm.pam.authenticate(wldm.secret.SecretBytes(b"alice"), secret) is True
    assert secret.as_bytes() == b""
    assert calls == ["end"]


def test_password_conv_frees_partial_allocations_when_message_buffer_alloc_fails(monkeypatch):
    class FakeMessage:
        def __init__(self, style):
            self.contents = ctypes.Structure.__new__(wldm.pam.PamMessage)
            self.contents.msg_style = style

    calls = []
    response_buffer = ctypes.create_string_buffer(ctypes.sizeof(wldm.pam.PamResponse) * 2)
    password_buffer = ctypes.create_string_buffer(16)

    def fake_calloc(count, size):
        calls.append(("calloc", count, size))
        if len(calls) == 1:
            return ctypes.addressof(response_buffer)
        if len(calls) == 2:
            return ctypes.addressof(password_buffer)
        return 0

    monkeypatch.setattr(wldm.pam, "calloc", fake_calloc)
    monkeypatch.setattr(wldm.pam, "free", lambda ptr: calls.append(("free", ptr)))

    response = [None]
    password = ctypes.c_char_p(b"secret")
    messages = [FakeMessage(wldm.pam.PAM_PROMPT_ECHO_OFF), FakeMessage(wldm.pam.PAM_PROMPT_ECHO_OFF)]

    rc = wldm.pam._password_conv(2, messages, response, ctypes.cast(password, ctypes.c_void_p))

    assert rc == wldm.pam.PAM_CONV_ERR
    assert response[0] is None
    freed_ptrs = [
        entry[1].value if isinstance(entry[1], ctypes.c_void_p) else entry[1]
        for entry in calls
        if entry[0] == "free"
    ]
    assert len(freed_ptrs) == 2
    assert ctypes.addressof(response_buffer) in freed_ptrs


def test_pam_error_str_decodes_message(monkeypatch):
    monkeypatch.setattr(wldm.pam.libpam, "pam_strerror", lambda pamh, code: b"Success")

    assert wldm.pam.pam_error_str(None, 0) == "Success"


def test_pam_error_str_handles_none_and_exceptions(monkeypatch):
    monkeypatch.setattr(wldm.pam.libpam, "pam_strerror", lambda pamh, code: None)
    assert wldm.pam.pam_error_str(None, 7) == "pam error 7"

    monkeypatch.setattr(
        wldm.pam.libpam,
        "pam_strerror",
        lambda pamh, code: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    assert wldm.pam.pam_error_str(None, 9) == "PAM error code 9"


def test_start_pam_raises_on_failure(monkeypatch):
    monkeypatch.setattr(wldm.pam.libpam, "pam_start", lambda service, user, conv, pamh: 5)
    monkeypatch.setattr(wldm.pam, "pam_error_str", lambda pamh, code: f"err {code}")

    try:
        wldm.pam.start_pam("login", "alice")
    except RuntimeError as exc:
        assert "pam_start failed: 5 (err 5)" in str(exc)
    else:
        raise AssertionError("start_pam() should raise on PAM failure")


def test_start_pam_returns_handle_on_success(monkeypatch):
    monkeypatch.setattr(wldm.pam.libpam, "pam_start", lambda service, user, conv, pamh: 0)

    handle = wldm.pam.start_pam("login", "alice")

    assert isinstance(handle, ctypes.c_void_p)


def test_open_pam_session_raises_on_account_failure(monkeypatch):
    monkeypatch.setattr(wldm.pam.libpam, "pam_acct_mgmt", lambda pamh, flags: 3)
    monkeypatch.setattr(wldm.pam, "pam_error_str", lambda pamh, code: f"err {code}")

    try:
        wldm.pam.open_pam_session("pamh")
    except RuntimeError as exc:
        assert "pam_acct_mgmt failed: 3 (err 3)" in str(exc)
    else:
        raise AssertionError("open_pam_session() should raise on account failure")


def test_open_pam_session_raises_on_setcred_failure(monkeypatch):
    monkeypatch.setattr(wldm.pam.libpam, "pam_acct_mgmt", lambda pamh, flags: 0)
    monkeypatch.setattr(wldm.pam.libpam, "pam_setcred", lambda pamh, flags: 9)
    monkeypatch.setattr(wldm.pam, "pam_error_str", lambda pamh, code: f"err {code}")

    try:
        wldm.pam.open_pam_session("pamh")
    except RuntimeError as exc:
        assert "pam_setcred: 9 (err 9)" in str(exc)
    else:
        raise AssertionError("open_pam_session() should raise on setcred failure")


def test_open_pam_session_raises_on_open_session_failure(monkeypatch):
    monkeypatch.setattr(wldm.pam.libpam, "pam_acct_mgmt", lambda pamh, flags: 0)
    monkeypatch.setattr(wldm.pam.libpam, "pam_setcred", lambda pamh, flags: 0)
    monkeypatch.setattr(wldm.pam.libpam, "pam_open_session", lambda pamh, flags: 8)
    monkeypatch.setattr(wldm.pam, "pam_error_str", lambda pamh, code: f"err {code}")

    try:
        wldm.pam.open_pam_session("pamh")
    except RuntimeError as exc:
        assert "pam_open_session failed: 8 (err 8)" in str(exc)
    else:
        raise AssertionError("open_pam_session() should raise on open_session failure")


def test_open_pam_session_succeeds(monkeypatch):
    monkeypatch.setattr(wldm.pam.libpam, "pam_acct_mgmt", lambda pamh, flags: 0)
    monkeypatch.setattr(wldm.pam.libpam, "pam_setcred", lambda pamh, flags: 0)
    monkeypatch.setattr(wldm.pam.libpam, "pam_open_session", lambda pamh, flags: 0)

    assert wldm.pam.open_pam_session("pamh") is None


def test_open_pam_session_only_succeeds(monkeypatch):
    monkeypatch.setattr(wldm.pam.libpam, "pam_open_session", lambda pamh, flags: 0)

    assert wldm.pam.open_pam_session_only("pamh") is None


def test_open_pam_session_only_raises_on_failure(monkeypatch):
    monkeypatch.setattr(wldm.pam.libpam, "pam_open_session", lambda pamh, flags: 8)
    monkeypatch.setattr(wldm.pam, "pam_error_str", lambda pamh, code: f"err {code}")

    try:
        wldm.pam.open_pam_session_only("pamh")
    except RuntimeError as exc:
        assert "pam_open_session failed: 8 (err 8)" in str(exc)
    else:
        raise AssertionError("open_pam_session_only() should raise on open_session failure")


def test_set_pam_item_succeeds(monkeypatch):
    monkeypatch.setattr(wldm.pam.libpam, "pam_set_item", lambda pamh, item_type, value: 0)

    assert wldm.pam.set_pam_item("pamh", wldm.pam.PAM_TTY, "/dev/tty7") is None


def test_set_pam_item_raises_on_failure(monkeypatch):
    monkeypatch.setattr(wldm.pam.libpam, "pam_set_item", lambda pamh, item_type, value: 5)
    monkeypatch.setattr(wldm.pam, "pam_error_str", lambda pamh, code: f"err {code}")

    try:
        wldm.pam.set_pam_item("pamh", wldm.pam.PAM_TTY, "/dev/tty7")
    except RuntimeError as exc:
        assert "pam_set_item failed: 5 (err 5)" in str(exc)
    else:
        raise AssertionError("set_pam_item() should raise on failure")


def test_putenv_succeeds(monkeypatch):
    monkeypatch.setattr(wldm.pam.libpam, "pam_putenv", lambda pamh, entry: 0)

    assert wldm.pam.putenv("pamh", "XDG_SESSION_TYPE", "wayland") is None


def test_putenv_raises_on_failure(monkeypatch):
    monkeypatch.setattr(wldm.pam.libpam, "pam_putenv", lambda pamh, entry: 6)
    monkeypatch.setattr(wldm.pam, "pam_error_str", lambda pamh, code: f"err {code}")

    try:
        wldm.pam.putenv("pamh", "XDG_SESSION_TYPE", "wayland")
    except RuntimeError as exc:
        assert "pam_putenv failed: 6 (err 6)" in str(exc)
    else:
        raise AssertionError("putenv() should raise on failure")


def test_close_pam_session_raises_on_setcred_failure(monkeypatch):
    monkeypatch.setattr(wldm.pam.libpam, "pam_setcred", lambda pamh, flags: 4)
    monkeypatch.setattr(wldm.pam, "pam_error_str", lambda pamh, code: f"err {code}")

    try:
        wldm.pam.close_pam_session("pamh")
    except RuntimeError as exc:
        assert "pam_setcred: 4 (err 4)" in str(exc)
    else:
        raise AssertionError("close_pam_session() should raise on setcred failure")


def test_close_pam_session_raises_on_close_failure(monkeypatch):
    monkeypatch.setattr(wldm.pam.libpam, "pam_setcred", lambda pamh, flags: 0)
    monkeypatch.setattr(wldm.pam.libpam, "pam_close_session", lambda pamh, flags: 6)
    monkeypatch.setattr(wldm.pam, "pam_error_str", lambda pamh, code: f"err {code}")

    try:
        wldm.pam.close_pam_session("pamh")
    except RuntimeError as exc:
        assert "pam_close_session failed: 6 (err 6)" in str(exc)
    else:
        raise AssertionError("close_pam_session() should raise on close_session failure")


def test_close_pam_session_succeeds(monkeypatch):
    monkeypatch.setattr(wldm.pam.libpam, "pam_setcred", lambda pamh, flags: 0)
    monkeypatch.setattr(wldm.pam.libpam, "pam_close_session", lambda pamh, flags: 0)

    assert wldm.pam.close_pam_session("pamh") is None


def test_end_pam_calls_libpam_when_available(monkeypatch):
    calls = []

    monkeypatch.setattr(wldm.pam.libpam, "pam_end", lambda pamh, status: calls.append((pamh, status)))

    wldm.pam.end_pam("pamh")

    assert calls == [("pamh", wldm.pam.PAM_SUCCESS)]


def test_getenvlist_parses_valid_entries(monkeypatch):
    values = [
        b"LANG=C.UTF-8",
        b"BROKEN",
        b"XDG_SESSION_TYPE=wayland",
        None,
    ]

    monkeypatch.setattr(wldm.pam.libpam, "pam_getenvlist", lambda pamh: values)

    env = wldm.pam.getenvlist("pamh")

    assert env == {"LANG": "C.UTF-8", "XDG_SESSION_TYPE": "wayland"}


def test_getenvlist_handles_index_error(monkeypatch):
    class BrokenList:
        def __getitem__(self, idx):
            raise IndexError

    monkeypatch.setattr(wldm.pam.libpam, "pam_getenvlist", lambda pamh: BrokenList())

    assert wldm.pam.getenvlist("pamh") == {}
