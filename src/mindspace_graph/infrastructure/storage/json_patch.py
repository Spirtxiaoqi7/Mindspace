"""JSON Pointer and Patch operations shared by file-backed repositories."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


def json_pointer_tokens(path: str) -> list[str]:
    if not path.startswith("/"):
        raise ValueError("JSON pointer must start with /")
    return [token.replace("~1", "/").replace("~0", "~") for token in path[1:].split("/")]


def read_json_pointer(document: dict[str, Any], path: str) -> Any:
    current: Any = document
    for token in json_pointer_tokens(path):
        if isinstance(current, list):
            if token == "-":
                return None
            try:
                index = int(token)
            except ValueError:
                return None
            if index < 0 or index >= len(current):
                return None
            current = current[index]
        elif isinstance(current, dict):
            if token not in current:
                return None
            current = current[token]
        else:
            return None
    return deepcopy(current)


def apply_json_patch(document: dict[str, Any], op: str, path: str, value: Any = None) -> None:
    tokens = json_pointer_tokens(path)
    current: Any = document
    for token in tokens[:-1]:
        if isinstance(current, list):
            current = current[int(token)]
        else:
            current = current.setdefault(token, {})
    leaf = tokens[-1]
    if isinstance(current, list):
        if op == "add" and leaf == "-":
            current.append(value)
        elif op == "add":
            current.insert(int(leaf), value)
        elif op == "remove":
            current.pop(int(leaf))
        else:
            current[int(leaf)] = value
    elif op == "remove":
        current.pop(leaf, None)
    else:
        current[leaf] = value


__all__ = ["apply_json_patch", "json_pointer_tokens", "read_json_pointer"]
