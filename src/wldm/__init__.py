# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026  Alexey Gladkov <legion@kernel.org>

import argparse
import contextlib
import logging
import os
import stat
from typing import Iterator, TextIO


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


@contextlib.contextmanager
def open_secure_directory(path: str, mode: int = 0o755) -> Iterator[int]:
    if not path:
        raise RuntimeError("runtime directory path must not be empty")

    abspath = os.path.abspath(path)
    components = [part for part in abspath.split(os.path.sep) if part]
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC

    parent_fd = os.open(os.path.sep, flags)
    try:
        if not components:
            final_fd = os.dup(parent_fd)
        else:
            for component in components:
                try:
                    next_fd = os.open(component, flags, dir_fd=parent_fd)
                except FileNotFoundError:
                    os.mkdir(component, mode=mode if component == components[-1] else 0o755, dir_fd=parent_fd)
                    next_fd = os.open(component, flags, dir_fd=parent_fd)
                except OSError as exc:
                    raise RuntimeError(
                        f"refusing to use symlink or non-directory runtime path component: {component}"
                    ) from exc

                os.close(parent_fd)
                parent_fd = next_fd

            final_fd = os.dup(parent_fd)

        st = os.fstat(final_fd)
        if not stat.S_ISDIR(st.st_mode):
            raise RuntimeError(f"runtime path is not a directory: {abspath}")
        if st.st_uid != os.geteuid():
            raise RuntimeError(f"runtime directory has unexpected owner: {abspath}")
        if st.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
            raise RuntimeError(f"runtime directory is writable by non-owner: {abspath}")

        os.fchmod(final_fd, mode)
        try:
            yield final_fd
        finally:
            os.close(final_fd)
    finally:
        os.close(parent_fd)


def ensure_secure_directory(path: str, mode: int = 0o755) -> None:
    with open_secure_directory(path, mode=mode):
        return None


def open_secure_append_file(path: str, mode: int = 0o600) -> TextIO:
    logdir = os.path.dirname(path)
    basename = os.path.basename(path)
    if not basename:
        raise RuntimeError(f"invalid log file path: {path}")

    with open_secure_directory(logdir or ".", mode=0o755) as dir_fd:
        flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC

        fd = os.open(basename, flags, mode, dir_fd=dir_fd)

    st = os.fstat(fd)
    if not stat.S_ISREG(st.st_mode):
        os.close(fd)
        raise RuntimeError(f"refusing to use non-regular file for logging: {path}")

    os.fchmod(fd, mode)
    return os.fdopen(fd, "a", encoding="utf-8", buffering=1)


@contextlib.contextmanager
def open_regular_text_file(path: str, *,
                           max_size: int | None = None,
                           encoding: str = "utf-8") -> Iterator[TextIO]:
    flags = os.O_RDONLY

    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC

    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW

    fd = os.open(path, flags)
    try:
        st = os.fstat(fd)

        if not stat.S_ISREG(st.st_mode):
            raise RuntimeError(f"refusing to read non-regular file: {path}")

        if max_size is not None and st.st_size > max_size:
            raise OverflowError(f"refusing to read oversized file: {path}")

        f = os.fdopen(fd, "r", encoding=encoding)
        fd = -1

        with f:
            yield f
    finally:
        if fd >= 0:
            os.close(fd)


def resolve_config_path(path: str, *,
                        base_dir: str = "") -> str:
    if not path:
        return ""

    if os.path.isabs(path):
        # "/usr/libexec/wldm-session-pre" stays absolute, but collapse symlinks.
        return os.path.realpath(path)

    # "../scripts/wayland-session" or "helpers/pre-hook" resolve from the
    # directory that contains the loaded config file.
    return os.path.realpath(os.path.join(base_dir or ".", path))


def drop_privileges(username: str, uid: int, gid: int, workdir: str) -> None:
    # Switch to the target user and working directory.
    os.initgroups(username, gid)
    os.setgid(gid)
    os.setuid(uid)

    os.chdir(workdir)


def close_inherited_fds(keep_fds: tuple[int, ...] = ()) -> None:
    # Close inherited fds while preserving the descriptors the next exec path
    # is expected to keep alive explicitly.
    close_from = 3
    max_fd = os.sysconf("SC_OPEN_MAX")
    sorted_keep_fds = sorted(fd for fd in keep_fds if fd >= close_from)
    bounds = [close_from] + [x for fd in sorted_keep_fds for x in (fd, fd + 1)] + [max_fd]

    for i in range(0, len(bounds), 2):
        os.closerange(bounds[i], bounds[i + 1])


def setup_file_logger(logger: logging.Logger, level: int,
                      fmt: str, path: str) -> logging.Logger:
    formatter = logging.Formatter(fmt=fmt, datefmt="%H:%M:%S")

    handler = logging.StreamHandler(open_secure_append_file(path, mode=0o600))
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
