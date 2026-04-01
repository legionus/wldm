#!/usr/bin/env python
# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026  Alexey Gladkov <legion@kernel.org>

import ctypes
from ctypes import addressof, c_char_p, c_void_p, cast, create_string_buffer, sizeof
from typing import Any


class SecretBytes:
    __slots__ = ("_buffer", "_length")

    def __init__(self, data: bytes | bytearray | memoryview | str = b"") -> None:
        if isinstance(data, str):
            raw = data.encode("utf-8")
        else:
            raw = bytes(data)

        self._buffer = create_string_buffer(raw)
        self._length = len(raw)

    def __len__(self) -> int:
        return self._length

    def __bool__(self) -> bool:
        return self._length > 0

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(<hidden>, len={self._length})"

    @classmethod
    def from_buffer(cls, buffer: Any, length: int) -> "SecretBytes":
        secret = cls.__new__(cls)
        secret._buffer = buffer
        secret._length = length

        return secret

    def as_bytes(self) -> bytes:
        return self._buffer.raw[:self._length]

    def as_c_char_p(self) -> c_char_p:
        return cast(self._buffer, c_char_p)

    def as_c_void_p(self) -> c_void_p:
        return cast(self._buffer, c_void_p)

    def clear(self) -> None:
        ctypes.memset(addressof(self._buffer), 0, sizeof(self._buffer))
        self._length = 0
