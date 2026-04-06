# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026  Alexey Gladkov <legion@kernel.org>

import ctypes
from types import SimpleNamespace

import wldm._gtk_ffi as gtk_ffi


def test_load_library_returns_none_when_library_is_missing(monkeypatch):
    monkeypatch.setattr(gtk_ffi, "find_library", lambda name: None)

    assert gtk_ffi._load_library("gtk-4") is None


def test_load_library_opens_resolved_library(monkeypatch):
    calls = []
    monkeypatch.setattr(gtk_ffi, "find_library", lambda name: "/usr/lib/libgtk-4.so.1")
    monkeypatch.setattr(gtk_ffi.ctypes, "CDLL", lambda path: calls.append(path) or "gtk")

    assert gtk_ffi._load_library("gtk-4") == "gtk"
    assert calls == ["/usr/lib/libgtk-4.so.1"]


def test_load_gtk_library_caches_loaded_library(monkeypatch):
    gtk_ffi._gtk = None
    calls = []
    library = SimpleNamespace(gtk_editable_get_text=SimpleNamespace())
    monkeypatch.setattr(gtk_ffi, "_load_library", lambda name: calls.append(name) or library)

    assert gtk_ffi._load_gtk_library() is library
    assert gtk_ffi._load_gtk_library() is library
    assert calls == ["gtk-4"]


def test_editable_pointer_handles_integer_and_capsule_errors(monkeypatch):
    assert gtk_ffi._editable_pointer(SimpleNamespace(__gpointer__=17)).value == 17

    monkeypatch.setattr(gtk_ffi, "_pycapsule_get_pointer", lambda pointer, name: (_ for _ in ()).throw(RuntimeError("boom")))
    assert gtk_ffi._editable_pointer(SimpleNamespace(__gpointer__=object())) is None
    assert gtk_ffi._editable_pointer(SimpleNamespace()) is None


def test_read_password_secret_uses_native_pointer_when_available(monkeypatch):
    gtk_ffi._gtk = None
    monkeypatch.setattr(gtk_ffi, "_load_gtk_library", lambda: SimpleNamespace(gtk_editable_get_text=lambda pointer: b"secret"))
    monkeypatch.setattr(gtk_ffi, "_editable_pointer", lambda editable: ctypes.c_void_p(123))
    monkeypatch.setattr(gtk_ffi, "strlen", lambda ptr: 6)

    secret = gtk_ffi.read_password_secret(SimpleNamespace(get_text=lambda: "fallback"))

    assert secret.as_bytes() == b"secret"
    secret.clear()


def test_read_password_secret_returns_empty_when_native_text_is_none(monkeypatch):
    gtk_ffi._gtk = None
    monkeypatch.setattr(gtk_ffi, "_load_gtk_library", lambda: SimpleNamespace(gtk_editable_get_text=lambda pointer: None))
    monkeypatch.setattr(gtk_ffi, "_editable_pointer", lambda editable: ctypes.c_void_p(123))

    secret = gtk_ffi.read_password_secret(SimpleNamespace(get_text=lambda: "fallback"))

    assert secret.as_bytes() == b""


def test_read_password_secret_falls_back_when_pointer_is_missing(monkeypatch):
    gtk_ffi._gtk = None
    monkeypatch.setattr(gtk_ffi, "_load_gtk_library", lambda: SimpleNamespace(gtk_editable_get_text=lambda pointer: b"secret"))
    monkeypatch.setattr(gtk_ffi, "_editable_pointer", lambda editable: None)

    secret = gtk_ffi.read_password_secret(SimpleNamespace(get_text=lambda: "fallback"))

    assert secret.as_bytes() == b"fallback"
