#!/bin/bash
# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026  Alexey Gladkov <legion@kernel.org>

set -euo pipefail

MISSING_DEPS=()

check_command()
{
	if ! command -v "$1" &> /dev/null; then
		MISSING_DEPS+=("$1")
	fi
}

check_package()
{
	if ! python3 -c "import $1" 2>/dev/null; then
		MISSING_DEPS+=("python3-$1 (or PyGObject)")
	fi
}

check_command "python3"
check_command "pkg-config"
check_package "gi"  # PyGObject

if [ ${#MISSING_DEPS[@]} -gt 0 ]; then
	echo "Missing dependencies:"
	for dep in "${MISSING_DEPS[@]}"; do
		echo "  - $dep"
	done
	exit 1
else
	echo "All dependencies found!"
fi
