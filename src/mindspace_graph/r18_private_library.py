"""Read-only, locally packaged R18 source material.

The bundled document is an encrypted DOCX resource, not a profile field and
not an API payload.  It is decrypted into process memory only while an R18
turn is assembled, then reduced to a small scene-selected overlay.  This is
deliberately light at-rest protection against casual file browsing; a desktop
application that can decrypt its own bundle cannot protect it from a user who
controls that machine.
"""

from __future__ import annotations

import hashlib
import hmac
import io
import os
import re
import zipfile
from functools import lru_cache
from pathlib import Path
from typing import Final
from xml.etree import ElementTree

_MAGIC: Final = b"MSR18\x01"
_NONCE_BYTES: Final = 16
_TAG_BYTES: Final = 32
_CHUNK_BYTES: Final = 32
_MAX_DOCX_BYTES: Final = 8 * 1024 * 1024
_MAX_PARAGRAPHS: Final = 256
_WORD_NAMESPACE: Final = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
RESOURCE_PATH = Path(__file__).resolve().parent / "resources" / "r18-private-library.bin"


def _bundle_key() -> bytes:
    # This is capability obfuscation, not a password vault.  Keep the key
    # derivation away from the resource so the package has no plaintext source
    # document, while remaining self-contained for offline installation.
    return hashlib.sha256(
        b"Mindspace" + bytes((82, 49, 56, 45, 108, 105, 98)) + b"local-readonly-v1"
    ).digest()


def _keystream(length: int, nonce: bytes) -> bytes:
    key = _bundle_key()
    blocks = []
    counter = 0
    while len(blocks) * _CHUNK_BYTES < length:
        blocks.append(hmac.digest(key, nonce + counter.to_bytes(4, "big"), "sha256"))
        counter += 1
    return b"".join(blocks)[:length]


def seal_payload(payload: bytes, *, nonce: bytes) -> bytes:
    """Create the build-time envelope; used by the sealed-resource regression test."""

    if len(nonce) != _NONCE_BYTES:
        raise ValueError("invalid nonce length")
    stream = _keystream(len(payload), nonce)
    ciphertext = bytes(value ^ stream[index] for index, value in enumerate(payload))
    body = _MAGIC + nonce + ciphertext
    return body + hmac.digest(_bundle_key(), body, "sha256")


def unseal_payload(envelope: bytes) -> bytes:
    """Verify and decrypt a package resource without writing plaintext to disk."""

    minimum = len(_MAGIC) + _NONCE_BYTES + _TAG_BYTES
    if len(envelope) < minimum or not envelope.startswith(_MAGIC):
        raise ValueError("invalid R18 library envelope")
    body, tag = envelope[:-_TAG_BYTES], envelope[-_TAG_BYTES:]
    expected = hmac.digest(_bundle_key(), body, "sha256")
    if not hmac.compare_digest(tag, expected):
        raise ValueError("R18 library integrity check failed")
    nonce = body[len(_MAGIC) : len(_MAGIC) + _NONCE_BYTES]
    ciphertext = body[len(_MAGIC) + _NONCE_BYTES :]
    stream = _keystream(len(ciphertext), nonce)
    return bytes(value ^ stream[index] for index, value in enumerate(ciphertext))


def _paragraphs_from_docx(payload: bytes) -> list[str]:
    if len(payload) > _MAX_DOCX_BYTES:
        raise ValueError("R18 library is unexpectedly large")
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        document = archive.read("word/document.xml")
    root = ElementTree.fromstring(document)
    paragraphs: list[str] = []
    for paragraph in root.iter(f"{_WORD_NAMESPACE}p"):
        text = "".join(node.text or "" for node in paragraph.iter(f"{_WORD_NAMESPACE}t"))
        text = re.sub(r"\s+", " ", text).strip()
        if text:
            paragraphs.append(text)
        if len(paragraphs) >= _MAX_PARAGRAPHS:
            break
    return paragraphs


@lru_cache(maxsize=1)
def load_private_r18_material() -> tuple[str, ...]:
    """Return local read-only paragraphs, or an empty tuple when not packaged."""

    override = os.environ.get("MINDSPACE_PRIVATE_R18_LIBRARY_PATH", "").strip()
    path = Path(override) if override else RESOURCE_PATH
    try:
        return tuple(_paragraphs_from_docx(unseal_payload(path.read_bytes())))
    except (OSError, ValueError, KeyError, zipfile.BadZipFile, ElementTree.ParseError):
        return ()


def private_library_status() -> dict[str, int | bool]:
    """Safe diagnostics only; the API never returns source text."""

    material = load_private_r18_material()
    return {"packaged": bool(material), "read_only": True, "paragraphs": len(material)}
