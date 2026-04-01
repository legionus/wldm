#!/usr/bin/env python
# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026  Alexey Gladkov <legion@kernel.org>

from dataclasses import dataclass
from typing import Dict, TextIO

import wldm


class IniParseError(ValueError):
    pass


@dataclass
class IniFile:
    sections: Dict[str, Dict[str, str]]

    def __getitem__(self, section: str) -> Dict[str, str]:
        return self.sections[section]

    def get_str(self, section: str, key: str, default: str = "") -> str:
        return str(self.get(section, key, default)).strip()

    def get(self, section: str, key: str, default: str = "") -> str:
        return self.sections.get(section, {}).get(key, default)

    def get_int(self, section: str, key: str, default: int = 0) -> int:
        if section not in self.sections or key not in self.sections[section]:
            return default
        return int(self.sections[section][key])

    def get_bool(self, section: str, key: str, default: bool = False) -> bool:
        if section not in self.sections or key not in self.sections[section]:
            return default
        value = self.sections[section][key].strip().lower()
        return value not in ["0", "false", "no", "off", ""]

    def section(self, name: str) -> Dict[str, str]:
        return self.sections.get(name, {})


def parse_ini_file(fileobj: TextIO, *,
                   allowed: Dict[str, set[str]],
                   ignore_unknown_sections: bool = False,
                   ignore_unknown_keys: bool = False) -> IniFile:
    parsed: Dict[str, Dict[str, str]] = {}
    current_section: str | None = None
    skip_section = False

    for lineno, raw_line in enumerate(fileobj, start=1):
        line = raw_line.strip()

        if not line or line.startswith("#") or line.startswith(";"):
            continue

        if line.startswith("[") and line.endswith("]"):
            name = line[1:-1].strip()

            if not name:
                raise IniParseError(f"empty section name at line {lineno}")

            if name not in allowed:
                if ignore_unknown_sections:
                    current_section = None
                    skip_section = True
                    continue

                raise IniParseError(f"unknown section {name!r} at line {lineno}")

            current_section = name
            skip_section = False
            parsed.setdefault(name, {})
            continue

        if "=" not in line:
            raise IniParseError(f"invalid line {lineno}: missing '='")

        if current_section is None:
            if skip_section:
                continue

            raise IniParseError(f"key/value outside a section at line {lineno}")

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()

        if not key:
            raise IniParseError(f"empty key at line {lineno}")

        if key not in allowed[current_section]:
            if ignore_unknown_keys:
                continue

            raise IniParseError(f"unknown key {key!r} in section {current_section!r} at line {lineno}")

        parsed[current_section][key] = value

    return IniFile(parsed)


def read_ini_file(path: str, *,
                  allowed: Dict[str, set[str]],
                  max_size: int,
                  ignore_unknown_sections: bool = False,
                  ignore_unknown_keys: bool = False) -> IniFile:
    with wldm.open_regular_text_file(path, max_size=max_size) as fileobj:
        return parse_ini_file(
            fileobj,
            allowed=allowed,
            ignore_unknown_sections=ignore_unknown_sections,
            ignore_unknown_keys=ignore_unknown_keys)
