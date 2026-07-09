# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026  Alexey Gladkov <legion@kernel.org>

import wldm
import wldm.lazy_imports


def test_unprivileged_loader_rejects_privileged_call(monkeypatch):
    calls = []

    @wldm.lazy_imports.unprivileged_loader
    def load_modules() -> tuple[str, ...]:
        calls.append("load")
        return ("ok",)

    monkeypatch.setattr(wldm, "_dropped_privileges", False)

    try:
        load_modules()
    except RuntimeError as exc:
        assert "requires dropped privileges" in str(exc)
    else:
        raise AssertionError("load_modules() should require dropped privileges")

    assert calls == []


def test_unprivileged_loader_caches_loaded_modules(monkeypatch):
    calls = []

    @wldm.lazy_imports.unprivileged_loader
    def load_modules() -> tuple[str, ...]:
        calls.append("load")
        return ("ok",)

    monkeypatch.setattr(wldm, "_dropped_privileges", True)
    monkeypatch.setattr(wldm.os, "geteuid", lambda: 1000)

    first = load_modules()
    second = load_modules()

    assert first is second
    assert first == ("ok",)
    assert calls == ["load"]


def test_unprivileged_loader_checks_context_before_returning_cached_value(monkeypatch):
    @wldm.lazy_imports.unprivileged_loader
    def load_modules() -> tuple[str, ...]:
        return ("ok",)

    monkeypatch.setattr(wldm, "_dropped_privileges", True)
    monkeypatch.setattr(wldm.os, "geteuid", lambda: 1000)

    assert load_modules() == ("ok",)

    monkeypatch.setattr(wldm, "_dropped_privileges", False)

    try:
        load_modules()
    except RuntimeError as exc:
        assert "requires dropped privileges" in str(exc)
    else:
        raise AssertionError("load_modules() should still require dropped privileges")
