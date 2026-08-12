from __future__ import annotations

from fastapi.testclient import TestClient

from mindspace_graph.asr_worker import create_worker_app


def test_asr_shutdown_is_loopback_token_protected(tmp_path) -> None:
    app = create_worker_app(tmp_path, "cpu", shutdown_token="expected-token")
    requested: list[bool] = []
    app.state.request_shutdown = lambda: requested.append(True)
    client = TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 50000))

    denied = client.post("/shutdown", headers={"X-Mindspace-Service-Token": "wrong-token"})
    assert denied.status_code == 403
    assert requested == []

    accepted = client.post("/shutdown", headers={"X-Mindspace-Service-Token": "expected-token"})
    assert accepted.status_code == 200
    assert accepted.json() == {"ok": True}
    assert requested == [True]
