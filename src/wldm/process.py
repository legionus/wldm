#!/usr/bin/env python
# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026  Alexey Gladkov <legion@kernel.org>

"""Shared process lifecycle helpers."""

import asyncio
import os
import signal

from asyncio.subprocess import Process as AsyncProcess
from contextlib import suppress

import wldm


logger = wldm.logger


def process_exit_status(status: int) -> int:
    """Map a wait status to a shell-style process exit code."""
    if os.WIFEXITED(status):
        return os.WEXITSTATUS(status)
    if os.WIFSIGNALED(status):
        return 128 + os.WTERMSIG(status)
    return wldm.EX_FAILURE


async def terminate_process(proc: AsyncProcess,
                            name: str,
                            timeout: float = 5.0) -> None:
    """Terminate one direct child process without process-group semantics."""
    if proc.returncode is not None:
        return

    logger.info("terminate %s (pid=%d)", name, proc.pid)
    proc.terminate()

    try:
        await asyncio.wait_for(proc.wait(), timeout=timeout)
        return
    except asyncio.TimeoutError:
        logger.critical("%s (pid=%d) did not stop after SIGTERM, sending SIGKILL", name, proc.pid)

    proc.kill()
    await proc.wait()


async def terminate_process_group(proc: AsyncProcess,
                                  name: str,
                                  timeout: float = 5.0) -> None:
    """Terminate a child process group by using the child pid as pgid."""
    if proc.returncode is not None:
        return

    logger.info("terminate %s (pid=%d)", name, proc.pid)

    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        return

    try:
        await asyncio.wait_for(proc.wait(), timeout=timeout)
        return
    except asyncio.TimeoutError:
        logger.critical("%s (pid=%d) did not stop after SIGTERM, sending SIGKILL", name, proc.pid)
    except ProcessLookupError:
        return

    with suppress(ProcessLookupError):
        os.killpg(proc.pid, signal.SIGKILL)

    with suppress(Exception):
        await proc.wait()


async def wait_for_stop_or_process(proc: AsyncProcess,
                                   stop_event: asyncio.Event) -> bool:
    """Wait until either a stop event is set or a process exits."""
    proc_task = asyncio.create_task(proc.wait())
    stop_task = asyncio.create_task(stop_event.wait())
    stopped = False

    try:
        done, _ = await asyncio.wait(
            {proc_task, stop_task},
            return_when=asyncio.FIRST_COMPLETED,
        )

        stopped = stop_task in done and stop_event.is_set()

    except Exception as e:
        logger.exception("unexpected failure while waiting for process pid=%d: %s", proc.pid, e)

    for task in [proc_task, stop_task]:
        if task.done():
            continue

        task.cancel()

        with suppress(asyncio.CancelledError):
            await task

    return stopped
