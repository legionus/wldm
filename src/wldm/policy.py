#!/usr/bin/env python
# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026  Alexey Gladkov <legion@kernel.org>

DEFAULT_SEAT = "seat0"
DEFAULT_TERM = "linux"
SESSION_TYPE_WAYLAND = "wayland"
SESSION_CLASS_USER = "user"
SESSION_CLASS_GREETER = "greeter"

GREETER_APP_ID = "org.wldm.greeter"
ACCOUNTS_SERVICE_USERS_DIR = "/var/lib/AccountsService/users"

SYSTEM_WAYLAND_SESSION_DIRS = ["/usr/share/wayland-sessions"]
USER_WAYLAND_SESSION_DIR = ".local/share/wayland-sessions"

ACCOUNT_SERVICE_MAX_FILE_SIZE = 64 * 1024
SESSION_ENTRY_MAX_FILE_SIZE = 64 * 1024
CONFIG_MAX_FILE_SIZE = 128 * 1024
