# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026  Alexey Gladkov <legion@kernel.org>

import argparse
import logging
from pathlib import Path
from types import SimpleNamespace

import wldm


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


def test_add_common_arguments_parses_standard_flags():
    parser = argparse.ArgumentParser(add_help=False)

    wldm.add_common_arguments(parser)
    args = parser.parse_args(["-vv", "-q"])

    assert args.verbose == 2
    assert args.quiet is True
