"""Offline maintenance commands for deterministic data repair."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from mindspace_graph.service import build_container
from mindspace_graph.settings import AppSettings


def main() -> None:
    parser = argparse.ArgumentParser(prog="mindspace-admin")
    parser.add_argument("command", choices=["check", "rebuild-memory"])
    parser.add_argument("--runtime", type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm", default="")
    args = parser.parse_args()
    settings = AppSettings.from_env()
    if args.runtime:
        settings.runtime_dir = args.runtime.resolve()
    container = build_container(settings)
    if args.command == "check":
        result = container.database.integrity_check()
    else:
        if args.apply and args.confirm != "REBUILD":
            parser.error("--apply requires --confirm REBUILD")
        result = container.memory_service.rebuild(dry_run=not args.apply)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
