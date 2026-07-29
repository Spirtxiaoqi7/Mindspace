from __future__ import annotations

import io
import subprocess
import zipfile
from pathlib import Path

from mindspace_graph.r18_private_library import (
    load_private_r18_material,
    private_library_status,
    seal_payload,
    unseal_payload,
)


def _docx(paragraphs: list[str]) -> bytes:
    body = "".join(
        f"<w:p><w:r><w:t>{paragraph}</w:t></w:r></w:p>" for paragraph in paragraphs
    )
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{body}</w:body></w:document>"
    )
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("word/document.xml", xml)
    return stream.getvalue()


def test_private_library_reads_a_sealed_docx_from_memory_only(tmp_path, monkeypatch):
    source = _docx(["全局素材第一段", "全局素材第二段"])
    sealed = tmp_path / "private.bin"
    sealed.write_bytes(seal_payload(source, nonce=b"0123456789abcdef"))
    monkeypatch.setenv("MINDSPACE_PRIVATE_R18_LIBRARY_PATH", str(sealed))
    load_private_r18_material.cache_clear()

    assert load_private_r18_material() == ("全局素材第一段", "全局素材第二段")
    assert private_library_status() == {"packaged": True, "read_only": True, "paragraphs": 2}
    assert not list(tmp_path.glob("*.docx"))
    load_private_r18_material.cache_clear()


def test_node_packager_and_core_unsealer_share_one_envelope_format(tmp_path):
    source = tmp_path / "source.docx"
    source.write_bytes(_docx(["只应从内存读取"] ))
    sealed = tmp_path / "private.bin"
    script = Path(__file__).parents[1] / "scripts" / "seal-r18-library.mjs"

    subprocess.run(["node", str(script), str(source), str(sealed)], check=True, capture_output=True)

    assert unseal_payload(sealed.read_bytes()) == source.read_bytes()


def test_tampered_private_library_is_rejected():
    envelope = bytearray(seal_payload(b"payload", nonce=b"0123456789abcdef"))
    envelope[-1] ^= 1

    try:
        unseal_payload(bytes(envelope))
    except ValueError as error:
        assert "integrity" in str(error)
    else:
        raise AssertionError("tampered envelope must not decrypt")
