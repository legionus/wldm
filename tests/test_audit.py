# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026  Alexey Gladkov <legion@kernel.org>

import os

import wldm.audit


def test_is_allowed_ctypes_target_accepts_expected_daemon_libraries():
    assert wldm.audit.is_allowed_ctypes_target("daemon", None) is True
    assert wldm.audit.is_allowed_ctypes_target("daemon", "pam") is True
    assert wldm.audit.is_allowed_ctypes_target("daemon", "/lib64/libpam.so.0") is False
    assert wldm.audit.is_allowed_ctypes_target("daemon", "/lib64/libc.so.6") is False


def test_is_allowed_ctypes_target_rejects_unexpected_library():
    assert wldm.audit.is_allowed_ctypes_target("daemon", "/tmp/libevil.so") is False


def test_is_trusted_system_library_path_accepts_root_owned_system_library(tmp_path, monkeypatch):
    libdir = tmp_path / "usr" / "lib64"
    libdir.mkdir(parents=True)
    libfile = libdir / "libpam.so.0"
    libfile.write_text("", encoding="utf-8")

    monkeypatch.setattr(wldm.audit, "SYSTEM_LIBRARY_DIRS", (str(tmp_path / "usr" / "lib64"),))
    monkeypatch.setattr(os, "stat", lambda path: os.stat_result((0o100644, 0, 0, 1, 0, 0, 0, 0, 0, 0)))

    assert wldm.audit.is_trusted_system_library_path(str(libfile)) is True


def test_is_trusted_system_library_path_rejects_non_system_path(tmp_path, monkeypatch):
    libfile = tmp_path / "tmp" / "libevil.so"
    libfile.parent.mkdir(parents=True)
    libfile.write_text("", encoding="utf-8")

    monkeypatch.setattr(wldm.audit, "SYSTEM_LIBRARY_DIRS", (str(tmp_path / "usr" / "lib64"),))

    assert wldm.audit.is_trusted_system_library_path(str(libfile)) is False


def test_setup_audit_hook_rejects_unexpected_ctypes_load(monkeypatch):
    hooks = []
    monkeypatch.setattr(wldm.audit.sys, "addaudithook", lambda hook: hooks.append(hook))
    monkeypatch.setattr(wldm.audit, "_active_role", None)

    wldm.audit.setup_audit_hook("daemon")

    try:
        hooks[0]("ctypes.dlopen", ("/tmp/libevil.so",))
    except RuntimeError as exc:
        assert "unexpected ctypes library load in daemon" in str(exc)
    else:
        raise AssertionError("audit hook should reject unexpected ctypes libraries")


def test_setup_audit_hook_ignores_allowed_ctypes_load(monkeypatch):
    hooks = []
    monkeypatch.setattr(wldm.audit.sys, "addaudithook", lambda hook: hooks.append(hook))
    monkeypatch.setattr(wldm.audit, "_active_role", None)

    wldm.audit.setup_audit_hook("daemon")

    hooks[0]("ctypes.dlopen", (None,))
    hooks[0]("ctypes.dlopen", ("libpam.so.0",))


def test_setup_audit_hook_rejects_role_change_in_one_process(monkeypatch):
    hooks = []
    monkeypatch.setattr(wldm.audit.sys, "addaudithook", lambda hook: hooks.append(hook))
    monkeypatch.setattr(wldm.audit, "_active_role", None)

    wldm.audit.setup_audit_hook("daemon")

    try:
        wldm.audit.setup_audit_hook("greeter")
    except RuntimeError as exc:
        assert "daemon" in str(exc)
        assert "greeter" in str(exc)
    else:
        raise AssertionError("setup_audit_hook() should reject role changes")
