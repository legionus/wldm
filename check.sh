#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026  Alexey Gladkov <legion@kernel.org>

set -euo pipefail
set -x

REAL_SCRIPT=$(realpath -e "${BASH_SOURCE[0]}")
SCRIPT_TOP="${SCRIPT_TOP:-$(dirname "${REAL_SCRIPT}")}"

export PYTHONPATH="${SCRIPT_TOP}/src"

find src/wldm -type f -name '*.py' -a \! -name '*_tab.py' |
	xargs -r pylint --disable=R,E0611 --disable=W0603,W0621,W0718 --disable=C0103,C0114,C0115,C0116,C0301,C0415,C3001

find src/wldm -type f -name '*.py' -a \! -name '*_tab.py' |
	xargs -r mypy --strict

if pytest --help 2>/dev/null | grep -q -- '--cov'; then
	pytest --cov=src/wldm --cov-report=term-missing --cov-fail-under=90 -q
else
	echo "pytest-cov is not available; running tests without coverage"
	pytest -q
fi
