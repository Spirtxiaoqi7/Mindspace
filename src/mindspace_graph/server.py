"""Console entry point for the packaged product server."""

from __future__ import annotations

import uvicorn

from mindspace_graph.settings import AppSettings


def main() -> None:
    settings = AppSettings.from_env()
    uvicorn.run(
        "mindspace_graph.api:create_app",
        factory=True,
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
    )


if __name__ == "__main__":
    main()
