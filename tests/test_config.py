# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026  Alexey Gladkov <legion@kernel.org>

import grp
import pwd
from pathlib import Path

import wldm.config
import wldm.policy


def test_read_config_uses_explicit_repo_config(monkeypatch):
    repo_root = Path(__file__).resolve().parents[1]

    monkeypatch.setenv("WLDM_CONFIG", str(repo_root / "tests" / "data" / "wldm.ini"))
    monkeypatch.setattr(pwd, "getpwuid", lambda uid: pwd.struct_passwd(
        ("fallback-user", "x", 1000, 1000, "", "/home/fallback-user", "/bin/sh")))
    monkeypatch.setattr(grp, "getgrgid", lambda gid: grp.struct_group(
        ("fallback-group", "x", 1000, [])))

    cfg = wldm.config.read_config()

    assert cfg["daemon"]["seat"] == "seat0"
    assert cfg["daemon"]["socket-path"] == "/run/wldm/greeter.sock"
    assert cfg["daemon"]["state-dir"] == ""
    assert cfg["daemon"]["log-path"] == ""
    assert cfg["daemon"]["suspend-command"] == ""
    assert cfg["daemon"]["hibernate-command"] == ""
    assert cfg["greeter"]["user"] == "gdm"
    assert cfg["greeter"]["group"] == "gdm"
    assert cfg["greeter"]["tty"] == "7"
    assert cfg["greeter"]["data-dir"] == "/usr/share/wldm"
    assert cfg["greeter"]["locale-dir"] == "/usr/share/locale"
    assert cfg["greeter"]["theme"] == "default"
    assert cfg["greeter"]["session-dirs"] == "/usr/share/wayland-sessions"
    assert cfg["greeter"]["user-session-dir"] == ".local/share/wayland-sessions"
    assert cfg["greeter"]["command"] == "cage -d -s -m last --"
    assert cfg["greeter"]["max-restarts"] == "3"
    assert cfg["greeter"]["user-sessions"] == "yes"
    assert cfg["greeter"]["log-path"] == ""
    assert cfg["session"]["pam-service"] == "login"
    assert cfg["session"]["execute"] == "/usr/share/wldm/scripts/wayland-session"
    assert cfg["session"]["pre-execute"] == ""
    assert cfg["session"]["post-execute"] == ""
    assert cfg["dbus"]["enabled"] == "no"
    assert cfg["dbus"]["user"] == "gdm"
    assert cfg["dbus"]["service"] == "org.freedesktop.DisplayManager"
    assert cfg["keyboard"]["rules"] == ""
    assert cfg["keyboard"]["model"] == ""
    assert cfg["keyboard"]["layout"] == ""
    assert cfg["keyboard"]["variant"] == ""
    assert cfg["keyboard"]["options"] == ""


def test_read_config_prefers_explicit_env_path(monkeypatch, tmp_path):
    config_file = tmp_path / "wldm.ini"
    config_file.write_text("[greeter]\nuser = env-user\ngroup = env-group\ntty = 9\n",
                           encoding="utf-8")

    monkeypatch.setenv("WLDM_CONFIG", str(config_file))

    cfg = wldm.config.read_config()

    assert cfg["greeter"]["user"] == "env-user"
    assert cfg["greeter"]["group"] == "env-group"
    assert cfg["greeter"]["tty"] == "9"


def test_read_config_sets_default_runtime_greeter_values(monkeypatch):
    monkeypatch.delenv("WLDM_CONFIG", raising=False)
    monkeypatch.setattr(pwd, "getpwuid", lambda uid: pwd.struct_passwd(
        ("fallback-user", "x", 1000, 1000, "", "/home/fallback-user", "/bin/sh")))
    monkeypatch.setattr(grp, "getgrgid", lambda gid: grp.struct_group(
        ("fallback-group", "x", 1000, [])))

    cfg = wldm.config.read_config()

    assert cfg["daemon"]["seat"] == "seat0"
    assert cfg["daemon"]["socket-path"] == "/run/wldm/greeter.sock"
    assert cfg["daemon"]["state-dir"] == ""
    assert cfg["daemon"]["log-path"] == ""
    assert cfg["daemon"]["suspend-command"] == ""
    assert cfg["daemon"]["hibernate-command"] == ""
    assert cfg["greeter"]["user"] == "fallback-user"
    assert cfg["greeter"]["group"] == "fallback-group"
    assert cfg["greeter"]["data-dir"] == ""
    assert cfg["greeter"]["locale-dir"] == ""
    assert cfg["greeter"]["theme"] == "default"
    assert cfg["greeter"]["session-dirs"] == "/usr/share/wayland-sessions"
    assert cfg["greeter"]["user-session-dir"] == ".local/share/wayland-sessions"
    assert cfg["greeter"]["user-sessions"] == "yes"
    assert cfg["greeter"]["log-path"] == ""
    assert cfg["session"]["pam-service"] == "login"
    assert cfg["session"]["execute"] == ""
    assert cfg["session"]["pre-execute"] == ""
    assert cfg["session"]["post-execute"] == ""
    assert cfg["dbus"]["enabled"] == "no"
    assert cfg["dbus"]["user"] == "fallback-user"
    assert cfg["dbus"]["service"] == "org.freedesktop.DisplayManager"
    assert cfg["keyboard"]["rules"] == ""
    assert cfg["keyboard"]["model"] == ""
    assert cfg["keyboard"]["layout"] == ""
    assert cfg["keyboard"]["variant"] == ""
    assert cfg["keyboard"]["options"] == ""


def test_read_config_sets_default_greeter_restart_limit(monkeypatch):
    monkeypatch.delenv("WLDM_CONFIG", raising=False)
    monkeypatch.setattr(pwd, "getpwuid", lambda uid: pwd.struct_passwd(
        ("fallback-user", "x", 1000, 1000, "", "/home/fallback-user", "/bin/sh")))
    monkeypatch.setattr(grp, "getgrgid", lambda gid: grp.struct_group(
        ("fallback-group", "x", 1000, [])))

    cfg = wldm.config.read_config()

    assert cfg["greeter"]["max-restarts"] == "3"


def test_read_config_loads_devel_overrides_when_selected_explicitly(monkeypatch):
    repo_root = Path(__file__).resolve().parents[1]

    monkeypatch.setenv("WLDM_CONFIG", str(repo_root / "config" / "wldm-devel.ini"))
    monkeypatch.setenv("WLDM_SOURCE_TREE", "1")
    monkeypatch.setattr(pwd, "getpwuid", lambda uid: pwd.struct_passwd(
        ("fallback-user", "x", 1000, 1000, "", "/home/fallback-user", "/bin/sh")))
    monkeypatch.setattr(grp, "getgrgid", lambda gid: grp.struct_group(
        ("fallback-group", "x", 1000, [])))

    cfg = wldm.config.read_config()

    assert cfg["daemon"]["socket-path"] == "/tmp/wldm/greeter.sock"
    assert cfg["daemon"]["state-dir"] == "/tmp/wldm/state"
    assert cfg["daemon"]["log-path"] == "/tmp/wldm/daemon.log"
    assert cfg["greeter"]["log-path"] == "/tmp/wldm/greeter.log"
    assert cfg["greeter"]["data-dir"] == str(repo_root / "data")
    assert cfg["greeter"]["locale-dir"] == str(repo_root / "locale")
    assert cfg["session"]["execute"] == str(repo_root / "data" / "scripts" / "wayland-session")


def test_read_config_keeps_relative_session_paths_outside_source_tree(monkeypatch):
    repo_root = Path(__file__).resolve().parents[1]

    monkeypatch.setenv("WLDM_CONFIG", str(repo_root / "config" / "wldm-devel.ini"))
    monkeypatch.delenv("WLDM_SOURCE_TREE", raising=False)
    monkeypatch.setattr(pwd, "getpwuid", lambda uid: pwd.struct_passwd(
        ("fallback-user", "x", 1000, 1000, "", "/home/fallback-user", "/bin/sh")))
    monkeypatch.setattr(grp, "getgrgid", lambda gid: grp.struct_group(
        ("fallback-group", "x", 1000, [])))

    cfg = wldm.config.read_config()

    assert cfg["session"]["execute"] == "../data/scripts/wayland-session"


def test_read_config_ignores_invalid_explicit_file(monkeypatch, tmp_path):
    config_file = tmp_path / "wldm.ini"
    config_file.write_text("not an ini file\n", encoding="utf-8")

    monkeypatch.setenv("WLDM_CONFIG", str(config_file))
    monkeypatch.setattr(pwd, "getpwuid", lambda uid: pwd.struct_passwd(
        ("fallback-user", "x", 1000, 1000, "", "/home/fallback-user", "/bin/sh")))
    monkeypatch.setattr(grp, "getgrgid", lambda gid: grp.struct_group(
        ("fallback-group", "x", 1000, [])))

    cfg = wldm.config.read_config()

    assert cfg["daemon"]["socket-path"] == "/run/wldm/greeter.sock"
    assert cfg["greeter"]["user"] == "fallback-user"


def test_read_config_ignores_oversized_explicit_file(monkeypatch, tmp_path):
    config_file = tmp_path / "wldm.ini"
    config_file.write_text("A" * (wldm.policy.CONFIG_MAX_FILE_SIZE + 1), encoding="utf-8")

    monkeypatch.setenv("WLDM_CONFIG", str(config_file))
    monkeypatch.setattr(pwd, "getpwuid", lambda uid: pwd.struct_passwd(
        ("fallback-user", "x", 1000, 1000, "", "/home/fallback-user", "/bin/sh")))
    monkeypatch.setattr(grp, "getgrgid", lambda gid: grp.struct_group(
        ("fallback-group", "x", 1000, [])))

    cfg = wldm.config.read_config()

    assert cfg["daemon"]["socket-path"] == "/run/wldm/greeter.sock"
    assert cfg["greeter"]["user"] == "fallback-user"
