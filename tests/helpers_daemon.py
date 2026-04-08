# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026  Alexey Gladkov <legion@kernel.org>

import asyncio
from types import SimpleNamespace


class DummyReader:
    def __init__(self, chunks):
        self.chunks = iter(chunks)

    async def readexactly(self, size):
        chunk = next(self.chunks, b"")
        if len(chunk) == size:
            return chunk
        partial = chunk[:size]
        raise asyncio.IncompleteReadError(partial=partial, expected=size)


class DummyWriter:
    def __init__(self, peer_pid=200, peer_uid=32, peer_gid=32):
        self.lines = []
        self.closed = False
        self.waited = False
        self.peer_pid = peer_pid
        self.peer_uid = peer_uid
        self.peer_gid = peer_gid

    def write(self, data):
        self.lines.append(data)

    async def drain(self):
        return None

    def close(self):
        self.closed = True

    async def wait_closed(self):
        self.waited = True

    def get_extra_info(self, name):
        if name != "socket":
            return None

        writer = self

        class DummySocket:
            def getsockopt(self, level, optname, buflen):
                return (
                    writer.peer_pid.to_bytes(4, "little")
                    + writer.peer_uid.to_bytes(4, "little")
                    + writer.peer_gid.to_bytes(4, "little")
                )

        return DummySocket()


class DummyAsyncProc:
    def __init__(self, pid=1234, returncode=0):
        self.pid = pid
        self.returncode = returncode
        self.wait_calls = 0

    async def wait(self):
        self.wait_calls += 1
        return self.returncode


class DummyProc:
    def __init__(self, pid=123, returncode=0):
        self.pid = pid
        self.returncode = returncode


def make_daemon_state(daemon_mod, internal_command="/srv/wldm/wldm.sh", *, seat="seat0", greeter_writer=False):
    state = daemon_mod.DaemonState(internal_command, 3, seat=seat)
    if greeter_writer:
        state.clients["greeter"].writer = DummyWriter()
    return state


def decode_last_client_message(greeter_protocol, state, name="greeter"):
    return greeter_protocol.decode_message(state.clients[name].writer.lines[-1])


def make_daemon_auth_session(daemon_mod, username="alice", ready=False):
    return daemon_mod.AuthSessionState(
        service="login",
        username=username,
        tty="/dev/tty7",
        proc=DummyAsyncProc(pid=6000, returncode=0),
        reader=SimpleNamespace(),
        writer=DummyWriter(),
        ready=ready,
    )


def make_worker_auth_session(daemon_auth_mod, username="alice", ready=False):
    return daemon_auth_mod.AuthSessionState(
        service="login",
        username=username,
        tty="/dev/tty7",
        proc=DummyProc(pid=321, returncode=0),
        reader=SimpleNamespace(),
        writer=DummyWriter(),
        ready=ready,
    )
