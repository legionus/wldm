# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026  Alexey Gladkov <legion@kernel.org>

import wldm.ipc_client as ipc_client
import wldm.protocol.greeter as greeter_protocol


def test_socket_client_uses_inherited_socket_fd(monkeypatch):
    calls = []

    class FakeSocket:
        def __init__(self, fileno):
            calls.append(fileno)

    monkeypatch.setattr(ipc_client.wldm, "inherited_socket_fd", lambda env_name: 11)
    monkeypatch.setattr(ipc_client.socket, "socket", FakeSocket)

    ipc_client.SocketClient.from_inherited_env()

    assert calls == [11]


def test_socket_client_writes_and_reads_protocol_messages(monkeypatch):
    writes = []
    expected = greeter_protocol.new_event(greeter_protocol.EVENT_SESSION_FINISHED, {})

    class FakeSocket:
        def sendall(self, data):
            writes.append(data)

    client = ipc_client.SocketClient.__new__(ipc_client.SocketClient)
    client.sock = FakeSocket()

    monkeypatch.setattr(ipc_client.greeter_protocol, "read_message_socket", lambda sock: expected)

    message = greeter_protocol.new_request(greeter_protocol.ACTION_REBOOT, {})
    client.write_message(message)

    assert greeter_protocol.decode_message(writes[0]) == message
    assert client.read_message() is expected


def test_socket_client_can_read_uses_select(monkeypatch):
    class FakeSocket:
        pass

    sock = FakeSocket()
    client = ipc_client.SocketClient.__new__(ipc_client.SocketClient)
    client.sock = sock

    monkeypatch.setattr(ipc_client.select, "select", lambda readable, writable, errors, timeout: ([sock], [], []))

    assert client.can_read() is True
