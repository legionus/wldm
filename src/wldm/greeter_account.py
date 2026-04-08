#!/usr/bin/env python
# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026  Alexey Gladkov <legion@kernel.org>

import os

import wldm
import wldm.inifile
import wldm.policy

logger = wldm.logger


def account_service_profile(username: str) -> dict[str, str] | None:
    """Return display-name and avatar data from one AccountsService profile."""
    if not username:
        return None

    path = os.path.join(wldm.policy.ACCOUNTS_SERVICE_USERS_DIR, username)
    try:
        data = wldm.inifile.read_ini_file(
            path,
            allowed={"User": {"RealName", "Icon"}},
            max_size=wldm.policy.ACCOUNT_SERVICE_MAX_FILE_SIZE,
            ignore_unknown_sections=True,
            ignore_unknown_keys=True,
        )
    except OverflowError:
        logger.warning("ignoring oversized AccountsService profile: %s", path)
        return None
    except (OSError, RuntimeError, UnicodeError, ValueError) as e:
        logger.debug("unable to read AccountsService profile %s: %s", path, e)
        return None

    display_name = data.get("User", "RealName", default="").strip()
    avatar_path = data.get("User", "Icon").strip()

    if not display_name and not avatar_path:
        return None

    if avatar_path and not os.path.isfile(avatar_path):
        avatar_path = ""

    return {
        "display_name": display_name or username,
        "avatar_path": avatar_path,
    }
