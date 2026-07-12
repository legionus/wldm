#!/usr/bin/env python
# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026  Alexey Gladkov <legion@kernel.org>

import importlib
import os
from typing import Final

import wldm

logger = wldm.logger

DEFAULT_BACKEND: Final = "gtk"
BACKENDS: Final = {
    "gtk": "wldm.greeter.gtk.app",
}


def selected_backend() -> str:
    """Return the configured greeter backend name."""
    return os.environ.get("WLDM_GREETER_BACKEND", DEFAULT_BACKEND).strip() or DEFAULT_BACKEND


def cmd_main() -> int:
    """Run the selected greeter frontend backend."""
    backend = selected_backend()
    module_name = BACKENDS.get(backend)

    if module_name is None:
        logger.critical("unknown greeter backend: %s", backend)
        return wldm.EX_FAILURE

    module = importlib.import_module(module_name)
    ret: int = module.cmd_main()
    return ret
