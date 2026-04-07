#!/bin/bash -efu
# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026  Alexey Gladkov <legion@kernel.org>

REAL_SCRIPT=$(realpath -e ${BASH_SOURCE[0]})
SCRIPT_TOP="${SCRIPT_TOP:-$(dirname ${REAL_SCRIPT})}"

exec systemd-run --unit=wldm-test --collect "$SCRIPT_TOP"/wldm.sh "$@"
