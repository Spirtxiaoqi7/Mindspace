"""Load an extracted Core package in an isolated runtime and probe its HTTP app."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def main() -> None:
    payload = Path(sys.argv[1]).resolve()
    runtime = Path(sys.argv[2]).resolve()
    source = payload / "src"
    if not (source / "mindspace_graph" / "server.py").is_file():
        raise RuntimeError("packaged Core source is missing")
    sys.path.insert(0, str(source))
    os.environ["MINDSPACE_RUNTIME_DIR"] = str(runtime)
    os.environ["MINDSPACE_PORT"] = "9876"

    from fastapi.testclient import TestClient
    from mindspace_graph.api import create_app

    with TestClient(create_app()) as client:
        health = client.get("/api/v1/diagnostics")
        health.raise_for_status()
        asset = client.get("/assets/characters/placeholder-1.webp")
        asset.raise_for_status()
    result = {
        "ok": bool(health.json()["ok"]),
        "version": health.json()["app"]["version"],
        "isolated_runtime": str(runtime),
        "anime_asset_status": asset.status_code,
        "anime_asset_bytes": len(asset.content),
    }
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))


if __name__ == "__main__":
    main()
