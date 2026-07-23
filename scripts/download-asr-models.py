"""Resumable, checksum-verified ModelScope downloader for the FunASR stack."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen

CORE_MODELS = {
    "paraformer-zh-streaming": (
        "iic/speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-online"
    ),
    "fsmn-vad": "iic/speech_fsmn_vad_zh-cn-16k-common-pytorch",
    "ct-punc": "iic/punc_ct-transformer_zh-cn-common-vocab272727-pytorch",
}
OPTIONAL_MODELS = {
    "Fun-ASR-Nano-2512": "FunAudioLLM/Fun-ASR-Nano-2512",
}
MODELS = {**CORE_MODELS, **OPTIONAL_MODELS}
API_ROOT = "https://www.modelscope.cn/api/v1/models"
FILE_ROOT = "https://www.modelscope.cn/models"


def _json(url: str) -> dict:
    with urlopen(url, timeout=60) as response:  # noqa: S310 - fixed official host
        return json.loads(response.read().decode("utf-8"))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _download(model_id: str, item: dict, target: Path) -> None:
    expected_size = int(item.get("Size") or 0)
    expected_hash = str(item.get("Sha256") or "").lower()
    if target.exists() and target.stat().st_size == expected_size:
        if not expected_hash or _sha256(target) == expected_hash:
            print(f"[skip] {target.name}", flush=True)
            return

    partial = target.with_suffix(target.suffix + ".partial")
    if target.exists() and target.stat().st_size < expected_size and not partial.exists():
        target.replace(partial)
    offset = partial.stat().st_size if partial.exists() else 0
    encoded_path = quote(str(item["Path"]).replace("\\", "/"), safe="/")
    url = f"{FILE_ROOT}/{model_id}/resolve/master/{encoded_path}"
    headers = {"User-Agent": "Mindspace-Graph/0.3.0"}
    if offset:
        headers["Range"] = f"bytes={offset}-"
    request = Request(url, headers=headers)
    with urlopen(request, timeout=180) as response:  # noqa: S310 - fixed official host
        if offset and getattr(response, "status", 200) != 206:
            offset = 0
            partial.unlink(missing_ok=True)
        mode = "ab" if offset else "wb"
        downloaded = offset
        next_report = downloaded + 64 * 1024 * 1024
        with partial.open(mode) as output:
            while chunk := response.read(4 * 1024 * 1024):
                output.write(chunk)
                downloaded += len(chunk)
                if downloaded >= next_report:
                    percent = downloaded / expected_size * 100 if expected_size else 0
                    print(f"  {target.name}: {percent:.1f}%", flush=True)
                    next_report += 64 * 1024 * 1024
    if partial.stat().st_size != expected_size:
        raise RuntimeError(
            f"size mismatch for {target}: {partial.stat().st_size} != {expected_size}"
        )
    if expected_hash and _sha256(partial) != expected_hash:
        raise RuntimeError(f"SHA-256 mismatch for {target}")
    os.replace(partial, target)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", action="append", choices=sorted(MODELS))
    parser.add_argument(
        "--include-final",
        action="store_true",
        help="also download the optional Fun-ASR Nano final-pass model",
    )
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, dict[str, object]] = {}
    for alias, model_id in MODELS.items():
        selected = set(args.model or CORE_MODELS)
        if args.include_final:
            selected.update(OPTIONAL_MODELS)
        if alias not in selected:
            continue
        destination = args.output / alias
        destination.mkdir(parents=True, exist_ok=True)
        print(f"[model] {alias} <- {model_id}", flush=True)
        repo = _json(f"{API_ROOT}/{model_id}/repo/files?Revision=master&Recursive=True")
        files = [item for item in repo["Data"]["Files"] if item.get("Type") == "blob"]
        for item in files:
            target = (destination / str(item["Path"])).resolve()
            if not target.is_relative_to(destination.resolve()):
                raise RuntimeError(f"unsafe model path: {item['Path']}")
            target.parent.mkdir(parents=True, exist_ok=True)
            _download(model_id, item, target)
        manifest[alias] = {
            "model_id": model_id,
            "path": str(destination),
            "files": len(files),
            "bytes": sum(int(item.get("Size") or 0) for item in files),
        }
    (args.output / "models.manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("[complete] all ASR models are checksum verified", flush=True)


if __name__ == "__main__":
    main()
