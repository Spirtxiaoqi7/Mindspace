from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SYNC_SCRIPT = ROOT / "scripts" / "sync-version.mjs"
VERIFY_SCRIPT = ROOT / "scripts" / "verify-version-consistency.mjs"
FIXTURE_FILES = (
    "config/version.json",
    "config/core-release-allowlist.json",
    "pyproject.toml",
    "uv.lock",
    "src/mindspace_graph/version.py",
    "frontend/package.json",
    "frontend/package-lock.json",
    "desktop/package.json",
    "desktop/package-lock.json",
    "payload.json",
    "docs/release-history.json",
    "desktop/assets/runtime-manifest.json",
)


def sandbox(tmp_path: Path) -> Path:
    target = tmp_path / "repo"
    for relative in FIXTURE_FILES:
        source = ROOT / relative
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    return target


def run_node(script: Path, target: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["MINDSPACE_VERSION_ROOT"] = str(target)
    return subprocess.run(
        ["node", str(script), *args],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def read_json(target: Path, relative: str):
    return json.loads((target / relative).read_text(encoding="utf-8"))


def write_json(target: Path, relative: str, value: object) -> None:
    (target / relative).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def test_sync_generates_source_tree_paths_and_independent_verifier_accepts_them(tmp_path: Path) -> None:
    target = sandbox(tmp_path)
    result = run_node(SYNC_SCRIPT, target)
    assert result.returncode == 0, result.stderr

    allowlist = read_json(target, "config/core-release-allowlist.json")
    payload = read_json(target, "payload.json")
    expected = [item["path"] for item in allowlist["source_trees"]] + allowlist["runtime_files"]
    assert payload["targets"] == expected
    assert not any(item.isdigit() for item in payload["targets"])

    verified = run_node(VERIFY_SCRIPT, target)
    assert verified.returncode == 0, verified.stderr
    assert "release targets=31" in verified.stdout


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value.update(schema_version="9.0.0"),
        lambda value: value.update(source_trees={"path": "src\\mindspace_graph"}),
        lambda value: value["source_trees"][0].update(path=""),
        lambda value: value["source_trees"][0].update(path="C:\\Mindspace"),
        lambda value: value["source_trees"][0].update(path="src\\..\\outside"),
        lambda value: value["source_trees"][0].update(path="0"),
        lambda value: value["runtime_files"].append(value["source_trees"][0]["path"].replace("\\", "/").upper()),
    ],
)
def test_sync_fails_closed_for_invalid_allowlist_schema_or_paths(tmp_path: Path, mutate) -> None:
    target = sandbox(tmp_path)
    allowlist = read_json(target, "config/core-release-allowlist.json")
    mutate(allowlist)
    write_json(target, "config/core-release-allowlist.json", allowlist)

    result = run_node(SYNC_SCRIPT, target, "--check")
    assert result.returncode != 0


def test_independent_verifier_rejects_numeric_duplicate_and_mismatched_payload_targets(tmp_path: Path) -> None:
    target = sandbox(tmp_path)
    assert run_node(SYNC_SCRIPT, target).returncode == 0
    payload = read_json(target, "payload.json")
    payload["targets"] = ["0", payload["targets"][0], payload["targets"][0], *payload["targets"][2:]]
    write_json(target, "payload.json", payload)

    result = run_node(VERIFY_SCRIPT, target)
    assert result.returncode != 0
    assert "numeric array index" in result.stderr
    assert "duplicates" in result.stderr
    assert "exactly equal" in result.stderr


def test_independent_verifier_does_not_delegate_to_sync_script() -> None:
    source = VERIFY_SCRIPT.read_text(encoding="utf-8")
    assert "sync-version.mjs" not in source
    assert "spawnSync" not in source
