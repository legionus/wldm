# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026  Alexey Gladkov <legion@kernel.org>

import argparse
import sys

import wldm

logger = wldm.logger


def cmd_daemon(cmdargs: argparse.Namespace) -> int:
    import wldm.daemon
    return wldm.daemon.cmd_main(cmdargs)


def cmd_greeter(cmdargs: argparse.Namespace) -> int:
    import wldm.greeter
    return wldm.greeter.cmd_main(cmdargs)


def cmd_session(cmdargs: argparse.Namespace) -> int:
    import wldm.session
    return wldm.session.cmd_main(cmdargs)


def cmd_greeter_session(cmdargs: argparse.Namespace) -> int:
    import wldm.greeter_session
    return wldm.greeter_session.cmd_main(cmdargs)


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

    # command: session
    sp_description = """\
opens a session for the user.

"""
    sp = subparsers.add_parser("session",
                               formatter_class=argparse.RawTextHelpFormatter,
                               description=sp_description, help=sp_description,
                               epilog=epilog, add_help=False)
    sp.set_defaults(func=cmd_session)
    wldm.add_common_arguments(sp)
    sp.add_argument("username", help="user to login")
    sp.add_argument("prog", nargs='?', default="", help="script when booting into custom rootfs")
    sp.add_argument("args", nargs='*', default=[], help="optional <prog> arguments")

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
    sp.add_argument("prog", help="program to execute")
    sp.add_argument("args", nargs=argparse.REMAINDER, help="optional <prog> arguments")

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
