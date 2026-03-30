# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026  Alexey Gladkov <legion@kernel.org>

import wldm.wtmp


def test_tty_line_uses_device_basename():
    assert wldm.wtmp.tty_line("/dev/tty12") == "tty12"


def test_login_calls_logwtmp_with_encoded_fields(monkeypatch):
    calls = []

    monkeypatch.setattr(wldm.wtmp, "_logwtmp", lambda line, user, host: calls.append((line, user, host)))

    wldm.wtmp.login("/dev/tty7", "alice")

    assert calls == [(b"tty7", b"alice", b"")]


def test_logout_calls_logwtmp_with_empty_username(monkeypatch):
    calls = []

    monkeypatch.setattr(wldm.wtmp, "_logwtmp", lambda line, user, host: calls.append((line, user, host)))

    wldm.wtmp.logout("/dev/tty7")

    assert calls == [(b"tty7", b"", b"")]


def test_login_is_noop_when_logwtmp_is_unavailable(monkeypatch):
    debug_messages = []

    monkeypatch.setattr(wldm.wtmp, "_logwtmp", None)
    monkeypatch.setattr(wldm.wtmp.logger, "debug", lambda msg, *args: debug_messages.append(msg % args if args else msg))

    wldm.wtmp.login("/dev/tty7", "alice")

    assert debug_messages == ["wtmp support is not available"]
