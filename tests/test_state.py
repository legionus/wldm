# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026  Alexey Gladkov <legion@kernel.org>

from pathlib import Path

import wldm.inifile
import wldm.state


def test_load_last_session_file_returns_empty_for_empty_path():
    assert wldm.state.load_last_session_file("") == ("", "")


def test_load_last_session_file_ignores_invalid_state(monkeypatch):
    monkeypatch.setattr(
        wldm.state.wldm.inifile,
        "read_ini_file",
        lambda *args, **kwargs: (_ for _ in ()).throw(wldm.inifile.IniParseError("bad")),
    )

    assert wldm.state.load_last_session_file("/tmp/last-session") == ("", "")


def test_load_last_session_file_ignores_missing_file(monkeypatch):
    monkeypatch.setattr(
        wldm.state.wldm.inifile,
        "read_ini_file",
        lambda *args, **kwargs: (_ for _ in ()).throw(FileNotFoundError()),
    )

    assert wldm.state.load_last_session_file("/tmp/last-session") == ("", "")


def test_save_last_session_file_skips_incomplete_input(tmp_path):
    path = tmp_path / "last-session"

    wldm.state.save_last_session_file("", "alice", "sway")
    wldm.state.save_last_session_file(str(path), "", "sway")
    wldm.state.save_last_session_file(str(path), "alice", "")

    assert path.exists() is False


def test_load_and_save_last_session_dir_helpers(tmp_path):
    state_dir = tmp_path / "state"

    assert wldm.state.load_last_session("") == ("", "")

    wldm.state.save_last_session(str(state_dir), "alice", "sway")

    assert wldm.state.load_last_session(str(state_dir)) == ("alice", "sway")

    wldm.state.save_last_session("", "alice", "sway")
    assert Path(state_dir).exists() is True
