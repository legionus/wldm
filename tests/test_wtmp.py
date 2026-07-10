# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026  Alexey Gladkov <legion@kernel.org>

import wldm.wtmp
import wldm.libc.wtmp
import wldm.libc._ffi


def test_libc_require_library_raises_when_missing(monkeypatch):
    monkeypatch.setattr(wldm.libc._ffi.ctypes.util, "find_library", lambda name: None)

    try:
        wldm.libc._ffi.require_library("c")
    except RuntimeError as exc:
        assert "required library: c" in str(exc)
    else:
        raise AssertionError("_require_library() should fail when libc is missing")


def test_login_calls_logwtmp_with_encoded_fields(monkeypatch):
    calls = []

    monkeypatch.setattr(wldm.libc.wtmp, "logwtmp",
                        lambda line, user, host: calls.append((line, user, host)) or True)

    wldm.wtmp.login("/dev/tty7", "alice")

    assert calls == [(b"tty7", b"alice", b"")]


def test_logout_calls_logwtmp_with_empty_username(monkeypatch):
    calls = []

    monkeypatch.setattr(wldm.libc.wtmp, "logwtmp",
                        lambda line, user, host: calls.append((line, user, host)) or True)

    wldm.wtmp.logout("/dev/tty7")

    assert calls == [(b"tty7", b"", b"")]


def test_login_is_noop_when_logwtmp_is_unavailable(monkeypatch):
    debug_messages = []

    monkeypatch.setattr(wldm.libc.wtmp, "logwtmp", lambda line, user, host: False)
    monkeypatch.setattr(wldm.wtmp.logger, "debug", lambda msg, *args: debug_messages.append(msg % args if args else msg))

    wldm.wtmp.login("/dev/tty7", "alice")

    assert debug_messages == ["wtmp support is not available"]
