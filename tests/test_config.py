# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026  Alexey Gladkov <legion@kernel.org>

import grp
import pwd
from pathlib import Path

import wldm.config
import wldm.policy


def test_read_config_uses_repo_default_when_progname_is_set(monkeypatch):
    repo_root = Path(__file__).resolve().parents[1]

    monkeypatch.setenv("WLDM_PROGNAME", str(repo_root / "wldm.sh"))
    monkeypatch.delenv("WLDM_CONFIG", raising=False)
    monkeypatch.setattr(pwd, "getpwuid", lambda uid: pwd.struct_passwd(
        ("fallback-user", "x", 1000, 1000, "", "/home/fallback-user", "/bin/sh")))
    monkeypatch.setattr(grp, "getgrgid", lambda gid: grp.struct_group(
        ("fallback-group", "x", 1000, [])))

    cfg = wldm.config.read_config()

    assert cfg["daemon"]["seat"] == "seat0"
    assert cfg["daemon"]["socket-path"] == "/run/wldm/greeter.sock"
    assert cfg["daemon"]["log-path"] == ""
    assert cfg["daemon"]["suspend-command"] == ""
    assert cfg["daemon"]["hibernate-command"] == ""
    assert cfg["greeter"]["user"] == "gdm"
    assert cfg["greeter"]["group"] == "gdm"
    assert cfg["greeter"]["tty"] == "7"
    assert cfg["greeter"]["theme"] == "default"
    assert cfg["greeter"]["session-dirs"] == "/usr/share/wayland-sessions"
    assert cfg["greeter"]["user-session-dir"] == ".local/share/wayland-sessions"
    assert cfg["greeter"]["command"] == "cage -d -s -m last --"
    assert cfg["greeter"]["max-restarts"] == "3"
    assert cfg["greeter"]["user-sessions"] == "yes"
    assert cfg["greeter"]["log-path"] == ""
    assert cfg["session"]["pam-service"] == "login"
    assert cfg["session"]["command"] == "default"
    assert cfg["session"]["pre-command"] == ""
    assert cfg["session"]["post-command"] == ""


def test_read_config_prefers_explicit_env_path(monkeypatch, tmp_path):
    config_file = tmp_path / "wldm.ini"
    config_file.write_text("[greeter]\nuser = env-user\ngroup = env-group\ntty = 9\n",
                           encoding="utf-8")

    monkeypatch.setenv("WLDM_CONFIG", str(config_file))
    monkeypatch.setenv("WLDM_PROGNAME", str(Path(__file__).resolve().parents[1] / "wldm.sh"))

    cfg = wldm.config.read_config()

    assert cfg["greeter"]["user"] == "env-user"
    assert cfg["greeter"]["group"] == "env-group"
    assert cfg["greeter"]["tty"] == "9"


def test_read_config_sets_default_runtime_greeter_values(monkeypatch):
    monkeypatch.delenv("WLDM_CONFIG", raising=False)
    monkeypatch.delenv("WLDM_PROGNAME", raising=False)
    monkeypatch.setattr(pwd, "getpwuid", lambda uid: pwd.struct_passwd(
        ("fallback-user", "x", 1000, 1000, "", "/home/fallback-user", "/bin/sh")))
    monkeypatch.setattr(grp, "getgrgid", lambda gid: grp.struct_group(
        ("fallback-group", "x", 1000, [])))

    cfg = wldm.config.read_config()

    assert cfg["daemon"]["seat"] == "seat0"
    assert cfg["daemon"]["socket-path"] == "/run/wldm/greeter.sock"
    assert cfg["daemon"]["log-path"] == ""
    assert cfg["daemon"]["suspend-command"] == ""
    assert cfg["daemon"]["hibernate-command"] == ""
    assert cfg["greeter"]["theme"] == "default"
    assert cfg["greeter"]["session-dirs"] == "/usr/share/wayland-sessions"
    assert cfg["greeter"]["user-session-dir"] == ".local/share/wayland-sessions"
    assert cfg["greeter"]["user-sessions"] == "yes"
    assert cfg["greeter"]["log-path"] == ""
    assert cfg["session"]["pam-service"] == "login"
    assert cfg["session"]["command"] == "default"
    assert cfg["session"]["pre-command"] == ""
    assert cfg["session"]["post-command"] == ""


def test_read_config_sets_default_greeter_restart_limit(monkeypatch):
    monkeypatch.delenv("WLDM_CONFIG", raising=False)
    monkeypatch.delenv("WLDM_PROGNAME", raising=False)
    monkeypatch.setattr(pwd, "getpwuid", lambda uid: pwd.struct_passwd(
        ("fallback-user", "x", 1000, 1000, "", "/home/fallback-user", "/bin/sh")))
    monkeypatch.setattr(grp, "getgrgid", lambda gid: grp.struct_group(
        ("fallback-group", "x", 1000, [])))

    cfg = wldm.config.read_config()

    assert cfg["greeter"]["max-restarts"] == "3"


def test_read_config_loads_devel_overrides_when_selected_explicitly(monkeypatch):
    repo_root = Path(__file__).resolve().parents[1]

    monkeypatch.setenv("WLDM_CONFIG", str(repo_root / "config" / "wldm-devel.ini"))
    monkeypatch.delenv("WLDM_PROGNAME", raising=False)
    monkeypatch.setattr(pwd, "getpwuid", lambda uid: pwd.struct_passwd(
        ("fallback-user", "x", 1000, 1000, "", "/home/fallback-user", "/bin/sh")))
    monkeypatch.setattr(grp, "getgrgid", lambda gid: grp.struct_group(
        ("fallback-group", "x", 1000, [])))

    cfg = wldm.config.read_config()

    assert cfg["daemon"]["socket-path"] == "/tmp/wldm/greeter.sock"
    assert cfg["daemon"]["log-path"] == "/tmp/wldm/daemon.log"
    assert cfg["greeter"]["log-path"] == "/tmp/wldm/greeter.log"


def test_read_config_uses_installed_share_default(monkeypatch, tmp_path):
    config_dir = tmp_path / "share" / "wldm" / "config"
    config_dir.mkdir(parents=True)
    config_file = config_dir / "wldm.ini"
    config_file.write_text("[greeter]\nuser = share-user\n", encoding="utf-8")

    monkeypatch.delenv("WLDM_CONFIG", raising=False)
    monkeypatch.delenv("WLDM_PROGNAME", raising=False)
    monkeypatch.setattr(wldm.config.sys, "prefix", str(tmp_path))

    cfg = wldm.config.read_config()

    assert cfg["greeter"]["user"] == "share-user"


def test_read_config_ignores_invalid_explicit_file(monkeypatch, tmp_path):
    config_file = tmp_path / "wldm.ini"
    config_file.write_text("not an ini file\n", encoding="utf-8")

    monkeypatch.setenv("WLDM_CONFIG", str(config_file))
    monkeypatch.delenv("WLDM_PROGNAME", raising=False)
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
    monkeypatch.delenv("WLDM_PROGNAME", raising=False)
    monkeypatch.setattr(pwd, "getpwuid", lambda uid: pwd.struct_passwd(
        ("fallback-user", "x", 1000, 1000, "", "/home/fallback-user", "/bin/sh")))
    monkeypatch.setattr(grp, "getgrgid", lambda gid: grp.struct_group(
        ("fallback-group", "x", 1000, [])))

    cfg = wldm.config.read_config()

    assert cfg["daemon"]["socket-path"] == "/run/wldm/greeter.sock"
    assert cfg["greeter"]["user"] == "fallback-user"
