# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026  Alexey Gladkov <legion@kernel.org>

import wldm.logindefs


def test_read_values_parses_numbers_and_strings(monkeypatch, tmp_path):
    login_defs = tmp_path / "login.defs"
    login_defs.write_text(
        "# comment\n"
        "UID_MIN 1000\n"
        "UMASK 027\n"
        "ENV_PATH /usr/local/bin:/usr/bin\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(wldm.logindefs, "LOGIN_DEFS", str(login_defs))

    wldm.logindefs.read_values()

    assert wldm.logindefs.get_number("UID_MIN") == 1000
    assert wldm.logindefs.get_number("UMASK") == 0o27
    assert wldm.logindefs.get_string("ENV_PATH") == "/usr/local/bin:/usr/bin"


def test_get_bool_defaults_to_false(monkeypatch, tmp_path):
    login_defs = tmp_path / "login.defs"
    login_defs.write_text("MAIL_CHECK_ENAB yes\n", encoding="utf-8")

    monkeypatch.setattr(wldm.logindefs, "LOGIN_DEFS", str(login_defs))

    wldm.logindefs.read_values()

    assert wldm.logindefs.get_bool("MAIL_CHECK_ENAB") is True
    assert wldm.logindefs.get_bool("UNKNOWN_FLAG") is False
