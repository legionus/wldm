#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2025  Alexey Gladkov <legion@kernel.org>

REAL_SCRIPT=$(realpath -e ${BASH_SOURCE[0]})
SCRIPT_TOP="${SCRIPT_TOP:-$(dirname ${REAL_SCRIPT})}"

export PYTHONPATH="${SCRIPT_TOP}/src"

export WLDM_PROGNAME="$REAL_SCRIPT"
export WLDM_RESOURCES_PATH="${SCRIPT_TOP}/resources"
export WLDM_CONFIG="${WLDM_CONFIG:-${SCRIPT_TOP}/config/wldm-devel.ini}"

exec python3 "${SCRIPT_TOP}/src/wldm/command.py" "${@}"
