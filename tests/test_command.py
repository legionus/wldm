# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026  Alexey Gladkov <legion@kernel.org>

from types import SimpleNamespace
import io
import sys
import types

import wldm
import wldm.command
import wldm.daemon


def patch_role_setup(monkeypatch):
    monkeypatch.setattr(wldm.command, "set_process_title", lambda role: None)
    monkeypatch.setattr(wldm.command.wldm.audit, "setup_audit_hook", lambda role: None)


def test_setup_parser_defaults_to_daemon():
    parser = wldm.command.setup_parser()

    args = parser.parse_args([])

    assert args.func is wldm.command.cmd_daemon


def test_setup_parser_rejects_internal_subcommands():
    parser = wldm.command.setup_parser()

    try:
        parser.parse_args(["greeter"])
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("internal subcommands should not be public CLI")


def test_cmd_daemon_dispatches_to_module(monkeypatch):
    monkeypatch.setattr(wldm.daemon, "cmd_main", lambda ns: 17)
    patch_role_setup(monkeypatch)

    result = wldm.command.cmd_daemon(SimpleNamespace())

    assert result == 17


def test_run_internal_role_dispatches_to_module(monkeypatch):
    calls = []
    module = types.ModuleType("wldm.fake_role")
    module.cmd_main = lambda: calls.append("cmd_main") or 13

    monkeypatch.setattr(wldm.command, "set_process_title", lambda role: calls.append(("title", role)))
    monkeypatch.setattr(wldm.command.wldm.audit, "setup_audit_hook", lambda role: calls.append(("audit", role)))
    monkeypatch.setattr(
        wldm.command.importlib,
        "import_module",
        lambda module_name: calls.append(("import", module_name)) or module,
    )

    result = wldm.command.run_internal_role("fake-role", "wldm.fake_role")

    assert result == 13
    assert calls == [
        ("title", "fake-role"),
        ("audit", "fake-role"),
        ("import", "wldm.fake_role"),
        "cmd_main",
    ]


def test_set_process_title_is_noop_without_module(monkeypatch):
    monkeypatch.setitem(sys.modules, "setproctitle", None)

    wldm.command.set_process_title("daemon")


def test_set_process_title_uses_setproctitle_module(monkeypatch):
    calls = {}

    class DummyModule:
        @staticmethod
        def setproctitle(value):
            calls["title"] = value

    monkeypatch.setitem(sys.modules, "setproctitle", DummyModule())

    wldm.command.set_process_title("dbus-adapter")

    assert calls["title"] == "wldm [dbus-adapter]"


def test_internal_command_prefix_uses_module_entrypoint_when_not_in_source_tree(monkeypatch):
    monkeypatch.setattr(wldm.command.sys, "executable", "/usr/bin/python3")
    monkeypatch.setattr(wldm.command.sys, "flags", SimpleNamespace(isolated=1, safe_path=True))
    monkeypatch.setattr(wldm.command, "source_tree", "")

    assert wldm.command.internal_command_prefix() == [
        "/usr/bin/python3",
        "-I",
        "-P",
        "-m",
        "wldm.command",
    ]


def test_internal_command_prefix_uses_command_script_in_source_tree(monkeypatch):
    monkeypatch.setattr(wldm.command.sys, "executable", "/usr/bin/python3")
    monkeypatch.setattr(wldm.command.sys, "flags", SimpleNamespace(isolated=1, safe_path=True))
    monkeypatch.setattr(wldm.command, "source_tree", "/srv/wldm")
    monkeypatch.setattr(wldm.command, "__file__", "/srv/wldm/src/wldm/command.py")

    assert wldm.command.internal_command_prefix() == [
        "/usr/bin/python3",
        "-I",
        "-P",
        "/srv/wldm/src/wldm/command.py",
    ]


def test_internal_command_prefix_strips_pyc_suffix(monkeypatch):
    monkeypatch.setattr(wldm.command.sys, "executable", "/usr/bin/python3")
    monkeypatch.setattr(wldm.command.sys, "flags", SimpleNamespace(isolated=1, safe_path=True))
    monkeypatch.setattr(wldm.command, "source_tree", "/srv/wldm")
    monkeypatch.setattr(wldm.command, "__file__", "/srv/wldm/src/wldm/command.pyc")

    assert wldm.command.internal_command_prefix() == [
        "/usr/bin/python3",
        "-I",
        "-P",
        "/srv/wldm/src/wldm/command.py",
    ]


def test_command_module_bootstraps_source_tree(monkeypatch):
    module = types.ModuleType("wldm_bootstrap_test")
    module.__dict__["__name__"] = "wldm_bootstrap_test"
    module.__dict__["__file__"] = "/srv/wldm/src/wldm/command.py"
    inserted = []
    fake_sys = SimpleNamespace(path=inserted)
    fake_os = SimpleNamespace(
        environ={"WLDM_SOURCE_TREE": "/srv/wldm"},
        path=SimpleNamespace(join=lambda a, b: f"{a}/{b}"),
    )

    exec("source_tree = os.environ.get('WLDM_SOURCE_TREE', '').strip()\n"
         "if source_tree:\n"
         "    sys.path.insert(0, os.path.join(source_tree, 'src'))\n",
         {"os": fake_os, "sys": fake_sys})

    assert inserted == ["/srv/wldm/src"]


def test_cmd_dispatches_to_selected_handler(monkeypatch):
    args = SimpleNamespace(func=lambda ns: 23, verbose=0, quiet=False)

    monkeypatch.setattr(wldm.command, "setup_parser", lambda: SimpleNamespace(parse_args=lambda: args))
    monkeypatch.setattr(wldm.command.wldm, "setup_verbosity", lambda ns: None)

    assert wldm.command.cmd() == 23


def test_cmd_dispatches_internal_role_from_environment(monkeypatch):
    calls = []
    monkeypatch.setenv("WLDM_ROLE", "pam-worker")
    monkeypatch.setitem(wldm.command.INTERNAL_ROLES, "pam-worker", "wldm.fake_pam_worker")
    monkeypatch.setattr(
        wldm.command,
        "run_internal_role",
        lambda role, module_name: calls.append((role, module_name)) or 20,
    )
    monkeypatch.setattr(wldm.command.wldm, "setup_verbosity", lambda ns: calls.append(("verbosity", ns.verbose, ns.quiet)))

    assert wldm.command.cmd() == 20
    assert calls == [("verbosity", 0, False), ("pam-worker", "wldm.fake_pam_worker")]


def test_cmd_rejects_unknown_internal_role(monkeypatch):
    monkeypatch.setenv("WLDM_ROLE", "unknown")
    monkeypatch.setattr(wldm.command.wldm, "setup_verbosity", lambda ns: None)

    assert wldm.command.cmd() == wldm.command.wldm.EX_FAILURE


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
