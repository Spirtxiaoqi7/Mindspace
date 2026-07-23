from __future__ import annotations

import json

from mindspace_graph.product_database import ProductDatabase


def _sse(run_id: str, sequence: int, event: str, data: dict[str, object]) -> str:
    envelope = {
        "version": "1.0",
        "event": event,
        "seq": sequence,
        "run_id": run_id,
        "session_id": "session-1",
        "round": 1,
        "timestamp": "2026-07-23T00:00:00+00:00",
        "data": data,
    }
    return (
        f"id: {sequence}\nevent: {event}\n"
        f"data: {json.dumps(envelope, ensure_ascii=False)}\n\n"
    )


def test_restart_closes_running_run_without_reexecuting_and_keeps_partial(tmp_path):
    path = tmp_path / "product.db"
    database = ProductDatabase(path)
    database.create_conversation_run(
        run_id="restart-run", session_id="session-1", round_num=1
    )
    database.append_conversation_run_event(
        run_id="restart-run",
        sequence=1,
        event="run.accepted",
        payload=_sse("restart-run", 1, "run.accepted", {}),
    )
    database.checkpoint_conversation_run("restart-run", "已经生成的部分", 42)

    restarted = ProductDatabase(path)
    assert restarted.recover_interrupted_runs() == 1
    assert restarted.recover_interrupted_runs() == 0

    record = restarted.get_conversation_run("restart-run")
    assert record is not None
    assert record["status"] == "interrupted"
    assert record["partial_text"] == "已经生成的部分"
    assert record["terminal_event"] == "run.interrupted"
    replay = "".join(restarted.conversation_run_events("restart-run", 42))
    assert "event: response.replace" in replay
    assert '"reason": "process_recovery"' in replay
    assert "已经生成的部分" in replay
    assert "event: run.interrupted" in replay


def test_run_event_storage_is_idempotent_and_bounded_to_128_events(tmp_path):
    database = ProductDatabase(tmp_path / "product.db")
    database.create_conversation_run(
        run_id="bounded-run", session_id="session-1", round_num=1
    )
    for sequence in range(1, 141):
        payload = _sse(
            "bounded-run",
            sequence,
            "node.completed",
            {"node": f"node-{sequence}"},
        )
        database.append_conversation_run_event(
            run_id="bounded-run",
            sequence=sequence,
            event="node.completed",
            payload=payload,
        )
        if sequence == 140:
            database.append_conversation_run_event(
                run_id="bounded-run",
                sequence=sequence,
                event="node.completed",
                payload=payload,
            )

    with database.connection() as connection:
        count, first_sequence, last_sequence = connection.execute(
            """
            SELECT COUNT(*), MIN(sequence), MAX(sequence)
            FROM conversation_run_events WHERE run_id='bounded-run'
            """
        ).fetchone()
    assert count == 128
    assert first_sequence == 13
    assert last_sequence == 140
