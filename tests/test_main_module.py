# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026  Alexey Gladkov <legion@kernel.org>

import runpy


def test_main_module_calls_sys_exit(monkeypatch):
    calls = {}

    monkeypatch.setattr("wldm.command.cmd", lambda: 7)
    monkeypatch.setattr("sys.exit", lambda code: calls.setdefault("exit", code))

    runpy.run_module("wldm", run_name="__main__")

    assert calls["exit"] == 7
