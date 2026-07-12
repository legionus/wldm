# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026  Alexey Gladkov <legion@kernel.org>

import argparse
import importlib
import os
import sys

source_tree = os.environ.get("WLDM_SOURCE_TREE", "").strip()
if source_tree:
    sys.path.insert(0, os.path.join(source_tree, "src"))

# pylint: disable-next=wrong-import-position
import wldm
# pylint: disable-next=wrong-import-position
import wldm.audit

logger = wldm.logger


def internal_command_prefix() -> list[str]:
    """Build the Python command prefix used for internal helper subprocesses.

    Returns:
        A command prefix that restarts the current wldm code under the same
        interpreter hardening flags. Source-tree mode uses the command.py path
        with `WLDM_SOURCE_TREE` bootstrap support, while installed mode uses
        `-m wldm.command`.
    """
    prefix = [sys.executable]

    if getattr(sys.flags, "isolated", 0):
        prefix.append("-I")

    if getattr(sys.flags, "safe_path", False):
        prefix.append("-P")

    if source_tree:
        path = os.path.abspath(__file__ or "")

        if path.endswith((".pyc", ".pyo")):
            path = path[:-1]

        return [*prefix, path]

    return [*prefix, "-m", "wldm.command"]


def set_process_title(role: str) -> None:
    try:
        setproctitle = importlib.import_module("setproctitle")

    except Exception:
        return

    setproctitle.setproctitle(f"wldm [{role}]")


def cmd_daemon(cmdargs: argparse.Namespace) -> int:
    set_process_title("daemon")
    wldm.audit.setup_audit_hook("daemon")
    import wldm.daemon as wldm_daemon
    return wldm_daemon.cmd_main(cmdargs)


def run_internal_role(role: str, module_name: str) -> int:
    set_process_title(role)
    wldm.audit.setup_audit_hook(role)
    module = importlib.import_module(module_name)
    ret: int = getattr(module, "cmd_main")()
    return ret


INTERNAL_ROLES = {
    "greeter": "wldm.greeter.gtk.app",
    "user-session": "wldm.user_session",
    "greeter-session": "wldm.greeter_session",
    "dbus-adapter": "wldm.dbus_adapter",
    "pam-worker": "wldm.pam_worker",
}


def setup_parser() -> argparse.ArgumentParser:
    epilog = "Report bugs to authors."

    description = """\
The wldm is a display manager that implements all significant features.
"""
    parser = argparse.ArgumentParser(
            prog="wldm",
            formatter_class=argparse.RawTextHelpFormatter,
            description=description,
            epilog=epilog,
            add_help=False,
            allow_abbrev=True)

    parser.set_defaults(func=cmd_daemon)
    wldm.add_common_arguments(parser)
    parser.add_argument("--tty",
                        dest="tty", metavar="NUM", action="store", type=int,
                        default=None,
                        help="use tty device number.")

    return parser


def cmd() -> int:
    role = os.environ.get("WLDM_ROLE", "").strip()
    if role:
        wldm.setup_verbosity(argparse.Namespace(verbose=0, quiet=False))
        module_name = INTERNAL_ROLES.get(role)

        if module_name is None:
            logger.critical("unknown internal role: %s", role)
            return wldm.EX_FAILURE

        return run_internal_role(role, module_name)

    parser = setup_parser()
    cmdargs = parser.parse_args()

    wldm.setup_verbosity(cmdargs)

    if not hasattr(cmdargs, "func"):
        parser.print_help()
        return wldm.EX_FAILURE

    ret: int = cmdargs.func(cmdargs)

    return ret


if __name__ == '__main__':
    sys.exit(cmd())
