# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026  Alexey Gladkov <legion@kernel.org>

import ctypes
from types import SimpleNamespace

import wldm.pam
import wldm.pam._ffi as ffi
import wldm.pam.funcs as pam_funcs


def fake_libpam(monkeypatch, **kwargs):
    attrs = {"pam_strerror": lambda pamh, code: f"err {code}".encode()}
    attrs.update(kwargs)
    libpam = SimpleNamespace(**attrs)
    monkeypatch.setattr(ffi, "libpam", lambda: libpam)
    return libpam


def test_simple_conv_returns_success():
    assert pam_funcs._simple_conv(0, [], None, None) == wldm.pam.PAM_SUCCESS


def test_pam_does_not_expose_libpam_binding():
    assert not hasattr(wldm.pam, "libpam")


def test_pam_ffi_require_library_raises_when_missing(monkeypatch):
    monkeypatch.setattr(ffi, "find_library", lambda name: None)

    try:
        ffi._require_library("pam")
    except RuntimeError as exc:
        assert "required library: pam" in str(exc)
    else:
        raise AssertionError("_require_library() should fail when libpam is missing")


def test_pam_error_str_decodes_message(monkeypatch):
    fake_libpam(monkeypatch, pam_strerror=lambda pamh, code: b"Success")

    assert wldm.pam.pam_error_str(None, 0) == "Success"


def test_pam_error_str_handles_none_and_exceptions(monkeypatch):
    libpam = fake_libpam(monkeypatch, pam_strerror=lambda pamh, code: None)
    assert wldm.pam.pam_error_str(None, 7) == "pam error 7"

    libpam.pam_strerror = lambda pamh, code: (_ for _ in ()).throw(RuntimeError("boom"))
    assert wldm.pam.pam_error_str(None, 9) == "PAM error code 9"


def test_start_pam_raises_on_failure(monkeypatch):
    fake_libpam(monkeypatch, pam_start=lambda service, user, conv, pamh: 5)
    monkeypatch.setattr(wldm.pam, "pam_error_str", lambda pamh, code: f"err {code}")

    try:
        wldm.pam.start_pam("login", "alice")
    except RuntimeError as exc:
        assert "pam_start failed: 5 (err 5)" in str(exc)
    else:
        raise AssertionError("start_pam() should raise on PAM failure")


def test_start_pam_returns_handle_on_success(monkeypatch):
    fake_libpam(monkeypatch, pam_start=lambda service, user, conv, pamh: 0)

    handle = wldm.pam.start_pam("login", "alice")

    assert isinstance(handle, ctypes.c_void_p)


def test_open_pam_session_raises_on_account_failure(monkeypatch):
    fake_libpam(monkeypatch, pam_acct_mgmt=lambda pamh, flags: 3)
    monkeypatch.setattr(wldm.pam, "pam_error_str", lambda pamh, code: f"err {code}")

    try:
        wldm.pam.open_pam_session("pamh")
    except RuntimeError as exc:
        assert "pam_acct_mgmt failed: 3 (err 3)" in str(exc)
    else:
        raise AssertionError("open_pam_session() should raise on account failure")


def test_open_pam_session_raises_on_setcred_failure(monkeypatch):
    fake_libpam(monkeypatch, pam_acct_mgmt=lambda pamh, flags: 0, pam_setcred=lambda pamh, flags: 9)
    monkeypatch.setattr(wldm.pam, "pam_error_str", lambda pamh, code: f"err {code}")

    try:
        wldm.pam.open_pam_session("pamh")
    except RuntimeError as exc:
        assert "pam_setcred: 9 (err 9)" in str(exc)
    else:
        raise AssertionError("open_pam_session() should raise on setcred failure")


def test_open_pam_session_raises_on_open_session_failure(monkeypatch):
    fake_libpam(monkeypatch, pam_acct_mgmt=lambda pamh, flags: 0, pam_setcred=lambda pamh, flags: 0,
                pam_open_session=lambda pamh, flags: 8)
    monkeypatch.setattr(wldm.pam, "pam_error_str", lambda pamh, code: f"err {code}")

    try:
        wldm.pam.open_pam_session("pamh")
    except RuntimeError as exc:
        assert "pam_open_session failed: 8 (err 8)" in str(exc)
    else:
        raise AssertionError("open_pam_session() should raise on open_session failure")


def test_open_pam_session_succeeds(monkeypatch):
    fake_libpam(monkeypatch, pam_acct_mgmt=lambda pamh, flags: 0, pam_setcred=lambda pamh, flags: 0,
                pam_open_session=lambda pamh, flags: 0)

    assert wldm.pam.open_pam_session("pamh") is None


def test_open_pam_session_only_succeeds(monkeypatch):
    fake_libpam(monkeypatch, pam_open_session=lambda pamh, flags: 0)

    assert wldm.pam.open_pam_session_only("pamh") is None


def test_open_pam_session_only_raises_on_failure(monkeypatch):
    fake_libpam(monkeypatch, pam_open_session=lambda pamh, flags: 8)
    monkeypatch.setattr(wldm.pam, "pam_error_str", lambda pamh, code: f"err {code}")

    try:
        wldm.pam.open_pam_session_only("pamh")
    except RuntimeError as exc:
        assert "pam_open_session failed: 8 (err 8)" in str(exc)
    else:
        raise AssertionError("open_pam_session_only() should raise on open_session failure")


def test_set_pam_item_succeeds(monkeypatch):
    fake_libpam(monkeypatch, pam_set_item=lambda pamh, item_type, value: 0)

    assert wldm.pam.set_pam_item("pamh", wldm.pam.PAM_TTY, "/dev/tty7") is None


def test_set_pam_item_raises_on_failure(monkeypatch):
    fake_libpam(monkeypatch, pam_set_item=lambda pamh, item_type, value: 5)
    monkeypatch.setattr(wldm.pam, "pam_error_str", lambda pamh, code: f"err {code}")

    try:
        wldm.pam.set_pam_item("pamh", wldm.pam.PAM_TTY, "/dev/tty7")
    except RuntimeError as exc:
        assert "pam_set_item failed: 5 (err 5)" in str(exc)
    else:
        raise AssertionError("set_pam_item() should raise on failure")


def test_putenv_succeeds(monkeypatch):
    fake_libpam(monkeypatch, pam_putenv=lambda pamh, entry: 0)

    assert wldm.pam.putenv("pamh", "XDG_SESSION_TYPE", "wayland") is None


def test_putenv_raises_on_failure(monkeypatch):
    fake_libpam(monkeypatch, pam_putenv=lambda pamh, entry: 6)
    monkeypatch.setattr(wldm.pam, "pam_error_str", lambda pamh, code: f"err {code}")

    try:
        wldm.pam.putenv("pamh", "XDG_SESSION_TYPE", "wayland")
    except RuntimeError as exc:
        assert "pam_putenv failed: 6 (err 6)" in str(exc)
    else:
        raise AssertionError("putenv() should raise on failure")


def test_close_pam_session_raises_on_setcred_failure(monkeypatch):
    fake_libpam(monkeypatch, pam_setcred=lambda pamh, flags: 4)
    monkeypatch.setattr(wldm.pam, "pam_error_str", lambda pamh, code: f"err {code}")

    try:
        wldm.pam.close_pam_session("pamh")
    except RuntimeError as exc:
        assert "pam_setcred: 4 (err 4)" in str(exc)
    else:
        raise AssertionError("close_pam_session() should raise on setcred failure")


def test_close_pam_session_raises_on_close_failure(monkeypatch):
    fake_libpam(monkeypatch, pam_setcred=lambda pamh, flags: 0, pam_close_session=lambda pamh, flags: 6)
    monkeypatch.setattr(wldm.pam, "pam_error_str", lambda pamh, code: f"err {code}")

    try:
        wldm.pam.close_pam_session("pamh")
    except RuntimeError as exc:
        assert "pam_close_session failed: 6 (err 6)" in str(exc)
    else:
        raise AssertionError("close_pam_session() should raise on close_session failure")


def test_close_pam_session_succeeds(monkeypatch):
    fake_libpam(monkeypatch, pam_setcred=lambda pamh, flags: 0, pam_close_session=lambda pamh, flags: 0)

    assert wldm.pam.close_pam_session("pamh") is None


def test_end_pam_calls_libpam_when_available(monkeypatch):
    calls = []

    fake_libpam(monkeypatch, pam_end=lambda pamh, status: calls.append((pamh, status)))

    wldm.pam.end_pam("pamh")

    assert calls == [("pamh", wldm.pam.PAM_SUCCESS)]


def test_getenvlist_parses_valid_entries(monkeypatch):
    values = [
        b"LANG=C.UTF-8",
        b"BROKEN",
        b"XDG_SESSION_TYPE=wayland",
        None,
    ]

    fake_libpam(monkeypatch, pam_getenvlist=lambda pamh: values)

    env = wldm.pam.getenvlist("pamh")

    assert env == {"LANG": "C.UTF-8", "XDG_SESSION_TYPE": "wayland"}


def test_getenvlist_handles_index_error(monkeypatch):
    class BrokenList:
        def __getitem__(self, idx):
            raise IndexError

    fake_libpam(monkeypatch, pam_getenvlist=lambda pamh: BrokenList())

    assert wldm.pam.getenvlist("pamh") == {}
