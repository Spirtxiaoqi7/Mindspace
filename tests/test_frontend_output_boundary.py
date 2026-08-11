from pathlib import Path


ROOT = Path(__file__).parents[1]
WEB_PACKAGE = ROOT / "src" / "mindspace_graph" / "web"
SOURCE_SUFFIXES = {".py", ".pyi"}
FORBIDDEN_STATIC_DIRECTORIES = {"archive", "assets", "characters", "dist", "static"}
FORBIDDEN_BUILD_SUFFIXES = {".css", ".html", ".js", ".map"}


def test_vite_output_is_not_inside_a_python_package() -> None:
    config = (ROOT / "frontend" / "vite.config.ts").read_text(encoding="utf-8")
    assert "../src/mindspace_graph/static/app" in config
    assert (WEB_PACKAGE / "models.py").is_file()


def test_python_web_package_contains_no_frontend_build_output() -> None:
    for entry in WEB_PACKAGE.rglob("*"):
        relative = entry.relative_to(WEB_PACKAGE)
        if "__pycache__" in relative.parts:
            continue
        assert entry.name not in FORBIDDEN_STATIC_DIRECTORIES, entry
        if entry.is_file():
            assert entry.name == "py.typed" or entry.suffix in SOURCE_SUFFIXES, entry
            assert entry.suffix not in FORBIDDEN_BUILD_SUFFIXES, entry
