# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026  Alexey Gladkov <legion@kernel.org>

import argparse
import contextlib
import functools
import logging
import os
import socket
import stat
from typing import Callable, Dict, Iterator, Mapping, ParamSpec, TextIO, TypeVar


__VERSION__ = '1'

EX_SUCCESS = 0  # Successful exit status.
EX_FAILURE = 1  # Failing exit status.

logger = logging.getLogger("wldm")
_dropped_privileges = False
_P = ParamSpec("_P")
_T = TypeVar("_T")
INTERNAL_ENV_ALLOWLIST = {
    "LANG",
    "LANGUAGE",
    "PATH",
    "WLDM_SOURCE_TREE",
    "WLDM_VERBOSITY",
}


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


def privileges_dropped() -> bool:
    """Return whether the process has passed the normal privilege drop point."""
    return _dropped_privileges and os.geteuid() != 0


def require_unprivileged(func: Callable[_P, _T]) -> Callable[_P, _T]:
    """Reject calls that should only happen after dropping privileges."""
    @functools.wraps(func)
    # pylint: disable-next=no-member
    def wrapper(*args: _P.args, **kwargs: _P.kwargs) -> _T:
        if not privileges_dropped():
            raise RuntimeError(f"{func.__name__} requires dropped privileges")
        return func(*args, **kwargs)

    return wrapper


def internal_helper_environ(extra: Mapping[str, str] | None = None) -> Dict[str, str]:
    """Build the minimal environment passed to internal helper processes."""
    env = {
        name: value
        for name, value in os.environ.items()
        if name in INTERNAL_ENV_ALLOWLIST or name.startswith("LC_")
    }

    if extra is not None:
        env.update(extra)

    return env


def inherited_socket_fd(env_name: str) -> int:
    """Return and validate one inherited stream socket fd from the environment."""
    value = os.environ.get(env_name, "").strip()

    try:
        fd = int(value)
    except ValueError as exc:
        raise RuntimeError(f"invalid or missing {env_name}") from exc

    if fd < 3:
        raise RuntimeError(f"{env_name} must refer to an inherited socket fd")

    try:
        st = os.fstat(fd)
    except OSError as exc:
        raise RuntimeError(f"{env_name} is not an open fd: {fd}") from exc

    if not stat.S_ISSOCK(st.st_mode):
        raise RuntimeError(f"{env_name} is not a socket fd: {fd}")

    sock = socket.fromfd(fd, socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock_type = sock.getsockopt(socket.SOL_SOCKET, socket.SO_TYPE)
    finally:
        sock.close()

    if sock_type != socket.SOCK_STREAM:
        raise RuntimeError(f"{env_name} must be a SOCK_STREAM socket")

    os.set_inheritable(fd, True)
    return fd


def drop_privileges(username: str, uid: int, gid: int, workdir: str) -> None:
    global _dropped_privileges

    # Switch to the target user and working directory.
    os.initgroups(username, gid)
    os.setgid(gid)
    os.setuid(uid)

    os.chdir(workdir)
    _dropped_privileges = True


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
