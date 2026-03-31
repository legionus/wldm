# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026  Alexey Gladkov <legion@kernel.org>

import io

import pytest

import wldm.inifile


def test_parse_ini_file_parses_allowed_sections_and_keys():
    parsed = wldm.inifile.parse_ini_file(
        io.StringIO(
            "[greeter]\n"
            "user = gdm\n"
            "tty = 7\n"
        ),
        allowed={"greeter": {"user", "tty"}},
    )

    assert parsed.sections == {"greeter": {"user": "gdm", "tty": "7"}}
    assert parsed.get("greeter", "user") == "gdm"
    assert parsed.get_str("greeter", "user") == "gdm"
    assert parsed.get("greeter", "missing", default="fallback") == "fallback"
    assert parsed.get_int("greeter", "tty", default=0) == 7


def test_parse_ini_file_get_bool_interprets_common_false_values():
    parsed = wldm.inifile.parse_ini_file(
        io.StringIO("[greeter]\nuser-sessions = no\n"),
        allowed={"greeter": {"user-sessions"}},
    )

    assert parsed.get_bool("greeter", "user-sessions", default=True) is False
    assert parsed.get_bool("greeter", "missing", default=True) is True


def test_get_str_strips_values():
    parsed = wldm.inifile.IniFile({"greeter": {"user": "  gdm  "}})

    assert parsed.get_str("greeter", "user") == "gdm"


def test_parse_ini_file_rejects_unknown_key():
    with pytest.raises(wldm.inifile.IniParseError):
        wldm.inifile.parse_ini_file(
            io.StringIO("[greeter]\nuser = gdm\nbad = value\n"),
            allowed={"greeter": {"user"}},
        )


def test_parse_ini_file_can_ignore_unknown_keys_and_sections():
    parsed = wldm.inifile.parse_ini_file(
        io.StringIO(
            "[User]\n"
            "RealName = Alice Doe\n"
            "Language = en_US.UTF-8\n"
            "[Extra]\n"
            "Ignored = value\n"
        ),
        allowed={"User": {"RealName", "Icon"}},
        ignore_unknown_sections=True,
        ignore_unknown_keys=True,
    )

    assert parsed.sections == {"User": {"RealName": "Alice Doe"}}
