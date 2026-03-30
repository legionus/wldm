# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026  Alexey Gladkov <legion@kernel.org>

from types import SimpleNamespace
import io

import wldm.command
import wldm.daemon


def test_setup_parser_defaults_to_daemon():
    parser = wldm.command.setup_parser()

    args = parser.parse_args([])

    assert args.func is wldm.command.cmd_daemon


def test_session_subcommand_parses_program_and_args():
    parser = wldm.command.setup_parser()

    args = parser.parse_args(["session", "alice", "startplasma-wayland", "--", "--debug"])

    assert args.func is wldm.command.cmd_session
    assert args.username == "alice"
    assert args.prog == "startplasma-wayland"
    assert args.args == ["--debug"]


def test_greeter_session_subcommand_parses_arguments():
    parser = wldm.command.setup_parser()

    args = parser.parse_args([
        "greeter-session",
        "--tty", "7",
        "--pam-service", "system-login",
        "gdm",
        "gdm",
        "cage",
        "-s",
        "-m",
        "last",
    ])

    assert args.func is wldm.command.cmd_greeter_session
    assert args.tty == 7
    assert args.pam_service == "system-login"
    assert args.username == "gdm"
    assert args.group == "gdm"
    assert args.prog == "cage"
    assert args.args == ["-s", "-m", "last"]


def test_cmd_daemon_dispatches_to_module(monkeypatch):
    monkeypatch.setattr(wldm.daemon, "cmd_main", lambda ns: 17)

    result = wldm.command.cmd_daemon(SimpleNamespace())

    assert result == 17


def test_cmd_dispatches_to_selected_handler(monkeypatch):
    args = SimpleNamespace(func=lambda ns: 23, verbose=0, quiet=False)

    monkeypatch.setattr(wldm.command, "setup_parser", lambda: SimpleNamespace(parse_args=lambda: args))
    monkeypatch.setattr(wldm.command.wldm, "setup_verbosity", lambda ns: None)

    assert wldm.command.cmd() == 23


def test_cmd_prints_help_and_fails_without_handler(monkeypatch):
    output = io.StringIO()
    args = SimpleNamespace(verbose=0, quiet=False)
    parser = SimpleNamespace(
        parse_args=lambda: args,
        print_help=lambda: output.write("help\n"),
    )

    monkeypatch.setattr(wldm.command, "setup_parser", lambda: parser)
    monkeypatch.setattr(wldm.command.wldm, "setup_verbosity", lambda ns: None)

    assert wldm.command.cmd() == wldm.command.wldm.EX_FAILURE
    assert output.getvalue() == "help\n"
