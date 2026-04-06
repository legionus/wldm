# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026  Alexey Gladkov <legion@kernel.org>

import ctypes

import wldm.secret


def test_secret_bytes_supports_string_input_and_repr():
    secret = wldm.secret.SecretBytes("secret")

    assert len(secret) == 6
    assert bool(secret) is True
    assert secret.as_bytes() == b"secret"
    assert repr(secret) == "SecretBytes(<hidden>, len=6)"


def test_secret_bytes_from_buffer_reuses_existing_storage():
    buffer = ctypes.create_string_buffer(b"token")
    secret = wldm.secret.SecretBytes.from_buffer(buffer, 5)

    assert secret.as_bytes() == b"token"
    assert secret.as_c_char_p().value == b"token"
    assert secret.as_c_void_p().value is not None


def test_secret_bytes_clear_zeros_contents():
    secret = wldm.secret.SecretBytes(b"secret")

    secret.clear()

    assert len(secret) == 0
    assert bool(secret) is False
    assert secret.as_bytes() == b""
