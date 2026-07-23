"""Safely extract third-party voice archives with deterministic legacy-name decoding."""

from __future__ import annotations

import argparse
import tarfile
import zipfile
from pathlib import Path


def safe_target(root: Path, name: str) -> Path:
    candidate = (root / name.replace("\\", "/")).resolve()
    if candidate != root and root not in candidate.parents:
        raise ValueError(f"archive path escapes target: {name}")
    return candidate


def extract_zip(source: Path, destination: Path, encoding: str) -> None:
    with zipfile.ZipFile(source, metadata_encoding=encoding or None) as archive:
        for info in archive.infolist():
            target = safe_target(destination, info.filename)
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as input_file, target.open("wb") as output_file:
                while chunk := input_file.read(1024 * 1024):
                    output_file.write(chunk)


def extract_tar(source: Path, destination: Path) -> None:
    with tarfile.open(source, "r:gz") as archive:
        for member in archive.getmembers():
            safe_target(destination, member.name)
            if member.issym() or member.islnk():
                raise ValueError(f"archive links are not allowed: {member.name}")
        archive.extractall(destination, filter="data")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--type", choices=("zip", "tar.gz"), required=True)
    parser.add_argument("--encoding", default="")
    arguments = parser.parse_args()
    source = arguments.source.resolve(strict=True)
    destination = arguments.destination.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    if arguments.type == "zip":
        extract_zip(source, destination, arguments.encoding)
    else:
        extract_tar(source, destination)


if __name__ == "__main__":
    main()
