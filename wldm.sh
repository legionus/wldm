#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2025  Alexey Gladkov <legion@kernel.org>

REAL_SCRIPT=$(realpath -e ${BASH_SOURCE[0]})
SCRIPT_TOP="${SCRIPT_TOP:-$(dirname ${REAL_SCRIPT})}"

export WLDM_SOURCE_TREE="${SCRIPT_TOP}"
export WLDM_CONFIG="${WLDM_CONFIG:-${SCRIPT_TOP}/config/wldm-devel.ini}"

unset PYTHONPATH

exec python3 -I -P "${SCRIPT_TOP}/src/wldm/command.py" -vvv "${@}"
