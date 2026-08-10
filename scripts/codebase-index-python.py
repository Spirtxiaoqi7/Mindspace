"""Extract Python module metadata for the generated codebase index."""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path


def _name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{_name(node.value)}.{node.attr}".strip(".")
    return ""


def inspect_file(path: Path) -> dict[str, object]:
    source = path.read_text(encoding="utf-8-sig")
    tree = ast.parse(source, filename=str(path))
    exports: list[str] = []
    imports: list[str] = []
    calls: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and not node.name.startswith("_"):
            exports.append(node.name)
        elif isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imports.append(node.module or ".")
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name) and target.id.isupper() and not target.id.startswith("_"):
                    exports.append(target.id)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            called = _name(node.func)
            if called:
                calls.add(called)
    return {
        "docstring": (ast.get_docstring(tree) or "").strip().splitlines()[0:2],
        "exports": exports[:16],
        "imports": list(dict.fromkeys(imports))[:12],
        "calls": sorted(calls)[:80],
    }


def main() -> None:
    paths = json.load(sys.stdin)
    result: dict[str, object] = {}
    for raw in paths:
        try:
            result[raw] = inspect_file(Path(raw))
        except (OSError, SyntaxError, UnicodeError) as exc:
            result[raw] = {"error": f"{type(exc).__name__}: {exc}"}
    json.dump(result, sys.stdout, ensure_ascii=False)


if __name__ == "__main__":
    main()
