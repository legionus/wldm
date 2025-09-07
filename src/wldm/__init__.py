# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026  Alexey Gladkov <legion@kernel.org>

import argparse
import logging
import os


__VERSION__ = '1-dev'

EX_SUCCESS = 0  # Successful exit status.
EX_FAILURE = 1  # Failing exit status.

logger = logging.getLogger("wldm")


def setup_logger(logger: logging.Logger, level: int,
                 fmt: str) -> logging.Logger:
    formatter = logging.Formatter(fmt=fmt, datefmt="%H:%M:%S")

    handler = logging.StreamHandler()
    handler.setLevel(level)
    handler.setFormatter(formatter)

    logger.setLevel(level)
    logger.addHandler(handler)

    return logger


def setup_file_logger(logger: logging.Logger, level: int,
                      fmt: str, path: str) -> logging.Logger:
    logdir = os.path.dirname(path)
    if logdir:
        os.makedirs(logdir, mode=0o755, exist_ok=True)

    formatter = logging.Formatter(fmt=fmt, datefmt="%H:%M:%S")

    handler = logging.FileHandler(path)
    handler.setLevel(level)
    handler.setFormatter(formatter)

    logger.addHandler(handler)

    return logger


def setup_verbosity(cmdargs: argparse.Namespace) -> None:
    verbosity = int(os.environ.get("WLDM_VERBOSITY", cmdargs.verbose))
    os.environ["WLDM_VERBOSITY"] = str(verbosity)

    match verbosity:
        case 0:
            level = logging.WARNING
        case 1:
            level = logging.INFO
        case _:
            level = logging.DEBUG

    if cmdargs.quiet:
        level = logging.CRITICAL

    setup_logger(logger, level=level, fmt="[%(asctime)s] %(message)s")


def add_common_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("-v", "--verbose",
                        dest="verbose", action='count', default=0,
                        help="print a message for each action.")
    parser.add_argument('-q', '--quiet',
                        dest="quiet", action='store_true', default=False,
                        help='output critical information only.')
    parser.add_argument("-V", "--version",
                        action='version',
                        help="show program's version number and exit.",
                        version=__VERSION__)
    parser.add_argument("-h", "--help",
                        action='help',
                        help="show this help message and exit.")
