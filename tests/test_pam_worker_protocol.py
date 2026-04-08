# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026  Alexey Gladkov <legion@kernel.org>

import asyncio
import socket

import wldm.pam_worker_protocol as pam_worker_protocol
from wldm.secret import SecretBytes


class ChunkReader:
    def __init__(self, chunks):
        self.chunks = iter(chunks)

    async def readexactly(self, size):
        chunk = next(self.chunks)
        assert len(chunk) == size
        return chunk


def test_encode_and_decode_all_worker_message_kinds():
    messages = [
        pam_worker_protocol.new_start("login", "alice", "/dev/tty7"),
        pam_worker_protocol.new_answer(SecretBytes(b"secret")),
        pam_worker_protocol.new_cancel(),
        pam_worker_protocol.new_prompt("secret", "Password:"),
        pam_worker_protocol.new_ready(),
        pam_worker_protocol.new_failed("Authentication failed"),
    ]

    decoded = [pam_worker_protocol.decode_message(pam_worker_protocol.encode_message(msg)) for msg in messages]

    assert decoded[0] == {"v": 1, "kind": "start", "service": "login", "username": "alice", "tty": "/dev/tty7"}
    assert decoded[1]["kind"] == "answer"
    assert decoded[1]["response"].as_bytes() == b"secret"
    assert decoded[2] == {"v": 1, "kind": "cancel"}
    assert decoded[3] == {"v": 1, "kind": "prompt", "style": "secret", "text": "Password:"}
    assert decoded[4] == {"v": 1, "kind": "ready"}
    assert decoded[5] == {"v": 1, "kind": "failed", "message": "Authentication failed"}


def test_encode_message_rejects_bad_answer_payload():
    try:
        pam_worker_protocol.encode_message({"v": 1, "kind": "answer", "response": object()})
    except pam_worker_protocol.ProtocolError as exc:
        assert "unsupported PAM worker answer payload" in str(exc)
    else:
        raise AssertionError("encode_message() should reject unsupported answer payloads")


def test_decode_message_rejects_trailing_bytes():
    frame = pam_worker_protocol.encode_message(pam_worker_protocol.new_ready()) + b"\x00"

    try:
        pam_worker_protocol.decode_message(frame)
    except pam_worker_protocol.ProtocolError as exc:
        assert "length mismatch" in str(exc)
    else:
        raise AssertionError("decode_message() should reject malformed frames")


def test_read_message_async_reads_one_frame():
    message = pam_worker_protocol.new_prompt("visible", "Code:")
    frame = pam_worker_protocol.encode_message(message)
    reader = ChunkReader([frame[:4], frame[4:]])

    decoded = asyncio.run(pam_worker_protocol.read_message_async(reader))

    assert decoded == {"v": 1, "kind": "prompt", "style": "visible", "text": "Code:"}


def test_read_message_socket_round_trips_blocking_socket():
    left, right = socket.socketpair()

    try:
        left.sendall(pam_worker_protocol.encode_message(pam_worker_protocol.new_failed("nope")))
        decoded = pam_worker_protocol.read_message_socket(right)
    finally:
        left.close()
        right.close()

    assert decoded == {"v": 1, "kind": "failed", "message": "nope"}


def test_read_message_async_returns_none_on_clean_eof():
    class EofReader:
        async def readexactly(self, size):
            raise asyncio.IncompleteReadError(partial=b"", expected=size)

    assert asyncio.run(pam_worker_protocol.read_message_async(EofReader())) is None


def test_read_message_socket_rejects_oversized_frame():
    left, right = socket.socketpair()

    try:
        left.sendall(pam_worker_protocol.FRAME_HEADER.pack(pam_worker_protocol.MAX_FRAME_BODY_LENGTH + 1))

        try:
            pam_worker_protocol.read_message_socket(right)
        except pam_worker_protocol.ProtocolError as exc:
            assert "too large" in str(exc)
        else:
            raise AssertionError("oversized frame should fail")
    finally:
        left.close()
        right.close()
