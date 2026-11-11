#
# SPDX-License-Identifier: Apache-2.0
# Copyright Contributors to the Dailies Notes Assistant Project.
#
"""Declarative version-ID extraction for RV source attributes.

Pure Python with no RV or Qt imports so it can be unit tested outside RV.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Mapping, Optional, Sequence

def default_version_id_config_path() -> str:
    """Return the packaged search-path config location.

    In the source tree the JSON sits next to this module. After ``rvpkg``
    install it lives under ``SupportFiles/dna/``.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    sibling = os.path.join(here, "version_id_paths.json")
    if os.path.isfile(sibling):
        return sibling
    installed = os.path.normpath(
        os.path.join(here, "..", "SupportFiles", "dna", "version_id_paths.json")
    )
    if os.path.isfile(installed):
        return installed
    return sibling


DEFAULT_CONFIG_PATH = default_version_id_config_path()


class InvalidVersionIdConfigError(ValueError):
    """Raised when a version-ID search-path config entry is malformed."""


def load_version_id_paths(path: Optional[str] = None) -> list[dict[str, Any]]:
    """Load ordered search-path steps from a JSON file."""
    config_path = path or default_version_id_config_path()
    with open(config_path, encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, list):
        raise InvalidVersionIdConfigError("version ID config must be a JSON array")
    return [validate_step(step, index) for index, step in enumerate(data)]


def validate_step(step: Any, index: int = 0) -> dict[str, Any]:
    if not isinstance(step, dict):
        raise InvalidVersionIdConfigError(f"step {index} must be an object")

    source = step.get("source")
    if source not in ("property", "metadata"):
        raise InvalidVersionIdConfigError(
            f"step {index} source must be 'property' or 'metadata'"
        )

    name = step.get("name")
    if not isinstance(name, str) or not name.strip():
        raise InvalidVersionIdConfigError(f"step {index} is missing name")

    coerce = step.get("as", "string")
    if coerce not in ("string", "int"):
        raise InvalidVersionIdConfigError(
            f"step {index} 'as' must be 'string' or 'int'"
        )

    match = step.get("match")
    if match is not None and not isinstance(match, str):
        raise InvalidVersionIdConfigError(f"step {index} match must be a string")

    return {
        "source": source,
        "name": name.strip(),
        "match": match,
        "as": coerce,
    }


def _lookup(
    bags: Mapping[str, Mapping[str, Any]], source: str, name: str
) -> Any:
    bag = bags.get(source) or {}
    if name in bag:
        return bag[name]
    lower = name.lower()
    for key, value in bag.items():
        if isinstance(key, str) and key.lower() == lower:
            return value
    return None


def _coerce(value: Any, coerce: str) -> Optional[str]:
    if value is None:
        return None
    if coerce == "int":
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            return str(value)
        text = str(value).strip()
        if not text:
            return None
        try:
            return str(int(text))
        except ValueError:
            return None
    text = str(value).strip()
    return text or None


def extract_version_id(
    properties: Optional[Mapping[str, Any]] = None,
    metadata: Optional[Mapping[str, Any]] = None,
    paths: Optional[Sequence[Mapping[str, Any]]] = None,
) -> Optional[str]:
    """Return the first version ID found by the ordered search paths."""
    bags = {
        "property": dict(properties or {}),
        "metadata": dict(metadata or {}),
    }
    steps = list(paths) if paths is not None else load_version_id_paths()

    for index, raw_step in enumerate(steps):
        step = validate_step(raw_step, index)
        value = _lookup(bags, step["source"], step["name"])
        if value is None:
            continue

        if step["match"]:
            text = str(value)
            found = re.search(step["match"], text)
            if not found or not found.groups():
                continue
            value = found.group(1)

        result = _coerce(value, step["as"])
        if result is not None:
            return result

    return None
