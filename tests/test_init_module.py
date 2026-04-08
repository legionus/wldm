# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026  Alexey Gladkov <legion@kernel.org>

import argparse
import logging
import os
import stat
from pathlib import Path
from types import SimpleNamespace

import wldm


def patch_open_secure_directory_primitives(
    monkeypatch,
    *,
    euid=0,
    abspath=None,
    st_mode=stat.S_IFDIR | 0o755,
    st_uid=0,
    on_close=None,
    on_fchmod=None,
):
    monkeypatch.setattr(wldm.os, "geteuid", lambda: euid)
    monkeypatch.setattr(wldm.os, "open", lambda *args, **kwargs: 4)
    monkeypatch.setattr(wldm.os, "dup", lambda fd: 5)
    monkeypatch.setattr(wldm.os, "close", on_close or (lambda fd: None))
    monkeypatch.setattr(wldm.os, "fchmod", on_fchmod or (lambda fd, mode: None))
    monkeypatch.setattr(wldm.os, "fstat", lambda fd: SimpleNamespace(st_mode=st_mode, st_uid=st_uid))
    if abspath is not None:
        monkeypatch.setattr(wldm.os.path, "abspath", abspath)


def test_setup_logger_adds_handler_with_requested_level():
    logger = logging.getLogger("wldm.test.setup_logger")
    logger.handlers.clear()

    configured = wldm.setup_logger(logger, level=logging.INFO, fmt="%(message)s")

    assert configured is logger
    assert logger.level == logging.INFO
    assert len(logger.handlers) == 1
    assert logger.handlers[0].level == logging.INFO


def test_setup_verbosity_uses_env_override(monkeypatch):
    calls = []

    monkeypatch.setenv("WLDM_VERBOSITY", "1")
    monkeypatch.setattr(
        wldm,
        "setup_logger",
        lambda logger, level, fmt: calls.append((logger.name, level, fmt)) or logger,
    )

    wldm.setup_verbosity(SimpleNamespace(verbose=3, quiet=False))

    assert calls == [("wldm", logging.INFO, "[%(asctime)s] %(message)s")]


def test_setup_verbosity_respects_quiet(monkeypatch):
    calls = []

    monkeypatch.delenv("WLDM_VERBOSITY", raising=False)
    monkeypatch.setattr(
        wldm,
        "setup_logger",
        lambda logger, level, fmt: calls.append((logger.name, level, fmt)) or logger,
    )

    wldm.setup_verbosity(SimpleNamespace(verbose=3, quiet=True))

    assert calls == [("wldm", logging.CRITICAL, "[%(asctime)s] %(message)s")]
    assert wldm.os.environ["WLDM_VERBOSITY"] == "3"


def test_setup_file_logger_creates_parent_dir_and_adds_handler(tmp_path):
    logger = logging.getLogger("wldm.test.setup_file_logger")
    logger.handlers.clear()
    log_path = tmp_path / "wldm" / "daemon.log"

    configured = wldm.setup_file_logger(logger, level=logging.INFO, fmt="%(message)s", path=str(log_path))

    assert configured is logger
    assert Path(log_path).parent.is_dir()
    assert len(logger.handlers) == 1
    assert logger.handlers[0].level == logging.INFO
    assert stat.S_IMODE(os.stat(log_path).st_mode) == 0o600


def test_ensure_secure_directory_rejects_symlink(tmp_path):
    target = tmp_path / "real"
    target.mkdir()
    link = tmp_path / "link"
    link.symlink_to(target)

    try:
        wldm.ensure_secure_directory(str(link))
    except RuntimeError as exc:
        assert "symlink" in str(exc)
    else:
        raise AssertionError("ensure_secure_directory() should reject symlinks")


def test_open_secure_append_file_rejects_symlink(tmp_path):
    target = tmp_path / "real.log"
    target.write_text("")
    link = tmp_path / "daemon.log"
    link.symlink_to(target)

    try:
        wldm.open_secure_append_file(str(link))
    except OSError:
        pass
    else:
        raise AssertionError("open_secure_append_file() should reject symlinks")


def test_add_common_arguments_parses_standard_flags():
    parser = argparse.ArgumentParser(add_help=False)

    wldm.add_common_arguments(parser)
    args = parser.parse_args(["-vv", "-q"])

    assert args.verbose == 2
    assert args.quiet is True


def test_open_secure_directory_rejects_empty_path():
    try:
        with wldm.open_secure_directory(""):
            raise AssertionError("open_secure_directory() should have failed")
    except RuntimeError as exc:
        assert "must not be empty" in str(exc)


def test_open_secure_directory_accepts_root_directory(monkeypatch):
    calls = []
    patch_open_secure_directory_primitives(
        monkeypatch,
        abspath=lambda path: "/",
        on_close=lambda fd: calls.append(("close", fd)),
        on_fchmod=lambda fd, mode: calls.append(("fchmod", fd, mode)),
    )

    with wldm.open_secure_directory("/") as dir_fd:
        assert dir_fd == 5

    assert calls == [("fchmod", 5, 0o755), ("close", 5), ("close", 4)]


def test_open_secure_directory_rejects_non_directory(monkeypatch):
    patch_open_secure_directory_primitives(monkeypatch, st_mode=stat.S_IFREG)

    try:
        with wldm.open_secure_directory("/tmp/test"):
            raise AssertionError("open_secure_directory() should reject non-directories")
    except RuntimeError as exc:
        assert "not a directory" in str(exc)


def test_open_secure_directory_rejects_unexpected_owner(monkeypatch):
    patch_open_secure_directory_primitives(monkeypatch, euid=1)

    try:
        with wldm.open_secure_directory("/tmp/test"):
            raise AssertionError("open_secure_directory() should reject unexpected owner")
    except RuntimeError as exc:
        assert "unexpected owner" in str(exc)


def test_open_secure_directory_rejects_non_owner_writable(monkeypatch):
    patch_open_secure_directory_primitives(monkeypatch, st_mode=stat.S_IFDIR | 0o777)

    try:
        with wldm.open_secure_directory("/tmp/test"):
            raise AssertionError("open_secure_directory() should reject writable directory")
    except RuntimeError as exc:
        assert "writable by non-owner" in str(exc)


def test_open_secure_append_file_rejects_invalid_path():
    try:
        wldm.open_secure_append_file("/")
    except RuntimeError as exc:
        assert "invalid log file path" in str(exc)
    else:
        raise AssertionError("open_secure_append_file() should reject invalid paths")


def test_open_secure_append_file_rejects_non_regular_file(monkeypatch):
    monkeypatch.setattr(wldm, "open_secure_directory", lambda path, mode=0o755: __import__("contextlib").nullcontext(5))
    monkeypatch.setattr(wldm.os, "open", lambda *args, **kwargs: 7)
    monkeypatch.setattr(wldm.os, "close", lambda fd: None)
    monkeypatch.setattr(wldm.os, "fchmod", lambda fd, mode: None)
    monkeypatch.setattr(wldm.os, "fstat", lambda fd: SimpleNamespace(st_mode=stat.S_IFDIR))

    try:
        wldm.open_secure_append_file("/tmp/wldm.log")
    except RuntimeError as exc:
        assert "non-regular file" in str(exc)
    else:
        raise AssertionError("open_secure_append_file() should reject non-regular files")


def test_open_regular_text_file_rejects_non_regular_file(monkeypatch):
    monkeypatch.setattr(wldm.os, "open", lambda *args, **kwargs: 3)
    monkeypatch.setattr(wldm.os, "close", lambda fd: None)
    monkeypatch.setattr(wldm.os, "fstat", lambda fd: SimpleNamespace(st_mode=stat.S_IFDIR, st_size=0))

    try:
        with wldm.open_regular_text_file("/tmp/not-regular"):
            raise AssertionError("open_regular_text_file() should reject non-regular files")
    except RuntimeError as exc:
        assert "non-regular file" in str(exc)


def test_resolve_config_path_handles_empty_and_absolute(tmp_path):
    absolute = tmp_path / "hook"

    assert wldm.resolve_config_path("") == ""
    assert wldm.resolve_config_path(str(absolute)) == str(absolute.resolve())


def test_drop_privileges_switches_groups_and_workdir(monkeypatch):
    calls = []
    monkeypatch.setattr(wldm.os, "initgroups", lambda username, gid: calls.append(("initgroups", username, gid)))
    monkeypatch.setattr(wldm.os, "setgid", lambda gid: calls.append(("setgid", gid)))
    monkeypatch.setattr(wldm.os, "setuid", lambda uid: calls.append(("setuid", uid)))
    monkeypatch.setattr(wldm.os, "chdir", lambda path: calls.append(("chdir", path)))

    wldm.drop_privileges("alice", 1000, 1000, "/home/alice")

    assert calls == [
        ("initgroups", "alice", 1000),
        ("setgid", 1000),
        ("setuid", 1000),
        ("chdir", "/home/alice"),
    ]


def test_setup_verbosity_defaults_to_warning(monkeypatch):
    calls = []

    monkeypatch.delenv("WLDM_VERBOSITY", raising=False)
    monkeypatch.setattr(
        wldm,
        "setup_logger",
        lambda logger, level, fmt: calls.append((logger.name, level, fmt)) or logger,
    )

    wldm.setup_verbosity(SimpleNamespace(verbose=0, quiet=False))

    assert calls == [("wldm", logging.WARNING, "[%(asctime)s] %(message)s")]
