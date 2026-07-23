from __future__ import annotations

from mindspace_graph.protocol import IncrementalResponseParser, ProtocolParser


def test_incremental_response_parser_handles_every_chunk_boundary():
    raw = '<response>你好，世界</response><json_update>{"trigger":"none"}</json_update>'
    for chunk_size in range(1, len("</response>") + 3):
        parser = IncrementalResponseParser()
        output: list[str] = []
        for index in range(0, len(raw), chunk_size):
            output.extend(parser.feed(raw[index : index + chunk_size]))
        assert "".join(output) == "你好，世界"
        assert parser.complete is True


def test_incremental_response_parser_never_leaks_json_update():
    parser = IncrementalResponseParser()
    chunks = [
        "prefix<res",
        "ponse>visible</res",
        'ponse><json_update>{"trigger":"none"}</json_update>',
    ]
    output = "".join(delta for chunk in chunks for delta in parser.feed(chunk))
    assert output == "visible"
    assert "json_update" not in output


def test_incremental_response_parser_accepts_plain_leading_reply_without_leaking_json():
    parser = IncrementalResponseParser()
    first = parser.feed("你好，")
    chunks = ["真实模型已连接。", '<json_update>{"trigger":"none"}</json_update>']

    output = "".join([*first, *(delta for chunk in chunks for delta in parser.feed(chunk))])

    assert first == ["你好，"]
    assert output == "你好，真实模型已连接。"
    assert "json_update" not in output
    assert parser.complete is True


def test_protocol_parser_recovers_plain_leading_response():
    raw = """你好！
<json_update>{"turn_id":"round_1","base_revisions":{},"trigger":"none","patches":[]}</json_update>"""

    protocol, errors = ProtocolParser().parse(raw)

    assert errors == []
    assert protocol is not None
    assert protocol.response == "你好！"
    assert protocol.json_update.trigger == "none"


def test_protocol_parser_accepts_fenced_json_and_dangling_response_close():
    raw = """你好！</response>
<json_update>```json
{"turn_id":"round_1","base_revisions":{},"trigger":"none","patches":[]}
```</json_update>"""

    protocol, errors = ProtocolParser().parse(raw)

    assert errors == []
    assert protocol is not None
    assert protocol.response == "你好！"


def test_model_terminator_is_removed_from_stream_and_final_response():
    raw = """<response>主动说点什么。<|im_end|></response>
<json_update>{"turn_id":"round_1","base_revisions":{},"trigger":"none","patches":[]}</json_update>"""
    protocol, errors = ProtocolParser().parse(raw)
    stream = IncrementalResponseParser()
    deltas: list[str] = []
    for chunk in (raw[:19], raw[19:31], raw[31:]):
        deltas.extend(stream.feed(chunk))

    assert errors == []
    assert protocol is not None
    assert protocol.response == "主动说点什么。"
    assert "".join(deltas) == "主动说点什么。"
