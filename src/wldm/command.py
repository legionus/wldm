# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026  Alexey Gladkov <legion@kernel.org>

import argparse
import importlib
import os
import sys

import wldm

logger = wldm.logger


def internal_command_prefix() -> list[str]:
    path = os.path.abspath(__file__ or "")

    if path.endswith((".pyc", ".pyo")):
        path = path[:-1]

    return [sys.executable, path]


def set_process_title(role: str) -> None:
    try:
        setproctitle = importlib.import_module("setproctitle")

    except Exception:
        return

    setproctitle.setproctitle(f"wldm [{role}]")


def cmd_daemon(cmdargs: argparse.Namespace) -> int:
    set_process_title("daemon")
    import wldm.daemon
    return wldm.daemon.cmd_main(cmdargs)


def cmd_greeter(cmdargs: argparse.Namespace) -> int:
    set_process_title("greeter")
    import wldm.greeter
    return wldm.greeter.cmd_main(cmdargs)


def cmd_user_session(cmdargs: argparse.Namespace) -> int:
    set_process_title("user-session")
    import wldm.user_session
    return wldm.user_session.cmd_main(cmdargs)


def cmd_greeter_session(cmdargs: argparse.Namespace) -> int:
    set_process_title("greeter-session")
    import wldm.greeter_session
    return wldm.greeter_session.cmd_main(cmdargs)


def cmd_dbus_adapter(cmdargs: argparse.Namespace) -> int:
    set_process_title("dbus-adapter")
    import wldm.dbus_adapter
    return wldm.dbus_adapter.cmd_main(cmdargs)


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

    subparsers = parser.add_subparsers(dest="subcmd", help="")

    # command: greeter
    sp_description = """\
allows selection of which application to start at login.

"""
    sp = subparsers.add_parser("greeter",
                               formatter_class=argparse.RawTextHelpFormatter,
                               description=sp_description, help=sp_description,
                               epilog=epilog, add_help=False)
    sp.set_defaults(func=cmd_greeter)
    wldm.add_common_arguments(sp)

    # command: user-session
    sp_description = """\
opens a session for the user.

"""
    sp = subparsers.add_parser("user-session",
                               formatter_class=argparse.RawTextHelpFormatter,
                               description=sp_description, help=sp_description,
                               epilog=epilog, add_help=False)
    sp.set_defaults(func=cmd_user_session)
    wldm.add_common_arguments(sp)
    sp.add_argument("username", help="user to login")

    # command: greeter-session
    sp_description = """\
opens a PAM-backed session for the greeter.

"""
    sp = subparsers.add_parser("greeter-session",
                               formatter_class=argparse.RawTextHelpFormatter,
                               description=sp_description, help=sp_description,
                               epilog=epilog, add_help=False)
    sp.set_defaults(func=cmd_greeter_session)
    wldm.add_common_arguments(sp)
    sp.add_argument("--tty", dest="tty", metavar="NUM", action="store", type=int,
                    required=True,
                    help="use tty device number.")
    sp.add_argument("--pam-service", dest="pam_service", action="store",
                    default="system-login",
                    help="PAM service used to create the greeter session.")
    sp.add_argument("username", help="greeter user")
    sp.add_argument("group", help="greeter group")

    # command: dbus-adapter
    sp_description = """\
bridges daemon state to an external D-Bus adapter.

"""
    sp = subparsers.add_parser("dbus-adapter",
                               formatter_class=argparse.RawTextHelpFormatter,
                               description=sp_description, help=sp_description,
                               epilog=epilog, add_help=False)
    sp.set_defaults(func=cmd_dbus_adapter)
    wldm.add_common_arguments(sp)
    sp.add_argument("username", help="adapter user")
    sp.add_argument("service", help="D-Bus service name")

    return parser


def cmd() -> int:
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
