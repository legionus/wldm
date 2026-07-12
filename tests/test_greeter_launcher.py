# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026  Alexey Gladkov <legion@kernel.org>

import types

import wldm
import wldm.greeter.launcher as greeter_launcher


def test_selected_backend_defaults_to_gtk(monkeypatch):
    monkeypatch.delenv("WLDM_GREETER_BACKEND", raising=False)

    assert greeter_launcher.selected_backend() == "gtk"


def test_selected_backend_uses_environment(monkeypatch):
    monkeypatch.setenv("WLDM_GREETER_BACKEND", "gtk")

    assert greeter_launcher.selected_backend() == "gtk"


def test_cmd_main_runs_selected_backend(monkeypatch):
    calls = []
    module = types.SimpleNamespace(cmd_main=lambda: calls.append("cmd_main") or 17)

    monkeypatch.setenv("WLDM_GREETER_BACKEND", "gtk")
    monkeypatch.setattr(
        greeter_launcher.importlib,
        "import_module",
        lambda module_name: calls.append(("import", module_name)) or module,
    )

    assert greeter_launcher.cmd_main() == 17
    assert calls == [("import", "wldm.greeter.gtk.app"), "cmd_main"]


def test_cmd_main_rejects_unknown_backend(monkeypatch):
    errors = []

    monkeypatch.setenv("WLDM_GREETER_BACKEND", "missing")
    monkeypatch.setattr(greeter_launcher.logger, "critical", lambda msg, *args: errors.append(msg % args))

    assert greeter_launcher.cmd_main() == wldm.EX_FAILURE
    assert errors == ["unknown greeter backend: missing"]
