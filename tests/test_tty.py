# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026  Alexey Gladkov <legion@kernel.org>

import wldm.tty


def test_open_console_returns_first_available_device(monkeypatch):
    opened = []

    def fake_open(path, flags):
        opened.append(path)
        if path != "/dev/console":
            raise OSError("nope")
        return 42

    monkeypatch.setattr(wldm.tty.os, "open", fake_open)

    assert wldm.tty.open_console() == 42
    assert opened == ["/dev/tty0", "/dev/systty", "/dev/console"]


def test_open_console_logs_errors_when_no_device_is_available(monkeypatch):
    criticals = []

    monkeypatch.setattr(
        wldm.tty.os,
        "open",
        lambda path, flags: (_ for _ in ()).throw(OSError(f"{path} denied")),
    )
    monkeypatch.setattr(
        wldm.tty.logger,
        "critical",
        lambda msg, *args: criticals.append(msg % args if args else msg),
    )

    assert wldm.tty.open_console() is None
    assert any("/dev/tty0" in message and "/dev/console" in message for message in criticals)


def test_device_name_formats_number():
    assert wldm.tty.device_name(7) == "/dev/tty7"


def test_available_returns_console_number(monkeypatch):
    def fake_ioctl(console, op, buf, mutate):
        buf[0] = 9
        return 0

    monkeypatch.setattr(wldm.tty.fcntl, "ioctl", fake_ioctl)

    assert wldm.tty.available(5) == 9


def test_available_returns_none_on_ioctl_error(monkeypatch):
    monkeypatch.setattr(
        wldm.tty.fcntl,
        "ioctl",
        lambda console, op, buf, mutate: (_ for _ in ()).throw(OSError("boom")),
    )

    assert wldm.tty.available(5) is None


def test_change_checks_bounds_and_success(monkeypatch):
    calls = []

    monkeypatch.setattr(
        wldm.tty.fcntl,
        "ioctl",
        lambda console, op, arg, mutate: calls.append((console, op, arg, mutate)) or 0,
    )

    assert wldm.tty.change(5, 7) is True
    assert wldm.tty.change(5, 0) is False
    assert calls == [
        (5, wldm.tty.VT_ACTIVATE, 7, True),
        (5, wldm.tty.VT_WAITACTIVE, 7, True),
    ]


def test_dealloc_and_make_control_tty_handle_errors(monkeypatch):
    monkeypatch.setattr(
        wldm.tty.fcntl,
        "ioctl",
        lambda console, op, arg, mutate: (_ for _ in ()).throw(OSError("boom")),
    )

    assert wldm.tty.dealloc(5, 7) is False
    assert wldm.tty.make_control_tty(5) is False


def test_ttydevice_initializes_switches_and_closes(monkeypatch):
    calls = []

    monkeypatch.setattr(wldm.tty, "available", lambda console: 8)
    monkeypatch.setattr(wldm.tty.os, "open", lambda path, flags: calls.append(("open", path, flags)) or 90)
    monkeypatch.setattr(wldm.tty.os, "fchown", lambda fd, uid, gid: calls.append(("fchown", fd, uid, gid)))
    monkeypatch.setattr(wldm.tty, "change", lambda console, num: calls.append(("change", console, num)) or True)
    monkeypatch.setattr(wldm.tty.os, "close", lambda fd: calls.append(("close", fd)))

    ttydev = wldm.tty.TTYdevice(11, 1001)
    ttydev.switch()
    ttydev.close()

    assert ttydev.filename == "/dev/tty8"
    assert ("open", "/dev/tty8", wldm.tty.os.O_RDWR) in calls
    assert ("fchown", 90, 1001, -1) in calls
    assert ("change", 11, 8) in calls
    assert ("fchown", 90, 0, -1) in calls
    assert ("close", 90) in calls


def test_ttydevice_raises_when_no_terminal_available(monkeypatch):
    monkeypatch.setattr(wldm.tty, "available", lambda console: None)

    try:
        wldm.tty.TTYdevice(11, 1001)
    except RuntimeError as exc:
        assert "No terminal available" in str(exc)
    else:
        raise AssertionError("TTYdevice() should fail when no tty is available")
