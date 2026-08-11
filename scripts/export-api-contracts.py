"""Deterministically export the Mindspace FastAPI OpenAPI contract."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
from pathlib import Path
from typing import Any


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPOSITORY_ROOT / "src"
OUTPUT_PATH = REPOSITORY_ROOT / "contracts" / "openapi" / "mindspace.openapi.json"


def _load_application_types():
    """Import the application only after the repository source path is available."""

    source = str(SOURCE_ROOT)
    if source not in sys.path:
        sys.path.insert(0, source)

    from mindspace_graph.api import create_app
    from mindspace_graph.settings import AppSettings

    return create_app, AppSettings


async def _build_schema() -> dict[str, Any]:
    create_app, app_settings = _load_application_types()
    with tempfile.TemporaryDirectory(prefix="mindspace-openapi-") as temporary:
        runtime_dir = Path(temporary) / "runtime"
        app = create_app(
            app_settings(
                runtime_dir=runtime_dir,
                llm_mode="demo",
                tts_provider="browser",
                asr_provider="browser",
                role_audit_enabled=False,
                context_compaction_enabled=False,
            )
        )
        async with app.router.lifespan_context(app):
            return app.openapi()


def _render_schema(schema: dict[str, Any]) -> str:
    return json.dumps(schema, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def _check(expected: str) -> int:
    if not OUTPUT_PATH.is_file():
        print(f"OpenAPI contract is missing: {OUTPUT_PATH}", file=sys.stderr)
        return 1
    actual = OUTPUT_PATH.read_text(encoding="utf-8")
    if actual != expected:
        print(
            "OpenAPI contract is stale; run scripts/export-api-contracts.py to regenerate it.",
            file=sys.stderr,
        )
        return 1
    print(f"OpenAPI contract is current: {OUTPUT_PATH}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Compare the in-memory export with the committed file without writing.",
    )
    arguments = parser.parse_args()

    rendered = _render_schema(asyncio.run(_build_schema()))
    if arguments.check:
        return _check(rendered)

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(rendered, encoding="utf-8", newline="\n")
    print(f"Exported OpenAPI contract: {OUTPUT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
