from __future__ import annotations

import asyncio
from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient

from mindspace_graph.adapters.in_memory import DeterministicLanguageModel
from mindspace_graph.adapters.openai_compatible import OpenAICompatibleLanguageModel
from mindspace_graph.api import create_app
from mindspace_graph.models import ChatRequest
from mindspace_graph.service import build_container
from mindspace_graph.settings import AppSettings
from mindspace_graph.tool_chain import ToolExecutionResult


def _settings(tmp_path, **overrides) -> AppSettings:
    values = {
        "runtime_dir": tmp_path / "runtime",
        "llm_mode": "demo",
        "tts_provider": "browser",
        "asr_provider": "browser",
        "role_audit_enabled": False,
        "context_compaction_enabled": False,
    }
    values.update(overrides)
    return AppSettings(**values)


class CountingModel(DeterministicLanguageModel):
    def __init__(self) -> None:
        super().__init__()
        self.stream_calls = 0

    def stream(self, messages, config):
        self.stream_calls += 1
        yield from super().stream(messages, config)


def test_sync_chat_reuses_completed_durable_run_and_returns_null_tool_execution(tmp_path) -> None:
    app = create_app(_settings(tmp_path))
    model = CountingModel()
    app.state.container.conversation.dependencies.llm = model
    client = TestClient(app)
    payload = {
        "message": "只执行一次",
        "session_id": "sync-idempotent",
        "round": 1,
        "retrieval": {"rag_enabled": False},
    }

    first = client.post("/api/v1/chat", json=payload, headers={"X-Request-ID": "same-sync-run"})
    second = client.post("/api/v1/chat", json=payload, headers={"X-Request-ID": "same-sync-run"})

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json() == second.json()
    assert first.json()["tool_execution"] is None
    assert model.stream_calls == 1
    assert len(app.state.container.sessions.load_all("sync-idempotent")) == 2
    app.state.container.conversation.close()


class NativeToolModel:
    def __init__(self) -> None:
        self.candidate_calls = 0
        self.final_calls = 0
        self._native_call = None

    def stream_with_tools(self, messages, config, *, tools, tool_choice="auto"):
        self.candidate_calls += 1
        self._native_call = {
            "id": "call_once",
            "type": "function",
            "function": {"name": "web", "arguments": '{"query":"DeepSeek 最新模型"}'},
        }
        if False:
            yield ""

    def take_native_tool_call(self):
        result = self._native_call
        self._native_call = None
        return result

    def stream(self, messages, config):
        self.final_calls += 1
        yield "<response>已完成一次查询。</response>"

    def take_usage(self):
        return None

    def take_provider_attempts(self):
        return []


class CountingWebCapability:
    def __init__(self) -> None:
        self.calls = 0

    def enabled(self, name: str) -> bool:
        return True

    def retrieval_decision(self, request, history=None):
        from mindspace_graph.web.models import RetrievalDecision

        return RetrievalDecision(mode="force", query=request.message)

    def auxiliary_tool_hint(self, request) -> str:
        return ""

    def execute_web(self, instruction):
        self.calls += 1
        return ToolExecutionResult(
            call_id=instruction.call_id,
            tool="web",
            level=3,
            status="success",
            parameter_summary=instruction.parameter_summary,
            source_count=1,
            data={"sources": [{"title": "结果", "url": "https://example.com"}]},
        )


def test_concurrent_duplicate_joins_one_graph_and_executes_tool_once(tmp_path) -> None:
    async def exercise() -> None:
        container = build_container(
            _settings(
                tmp_path,
                llm_base_url="https://api.deepseek.com",
                llm_api_key="local-test-key",
            )
        )
        model = NativeToolModel()
        capability = CountingWebCapability()
        container.conversation.dependencies.llm = model
        container.conversation.dependencies.capabilities = capability
        request = ChatRequest(
            message="帮我联网搜索 DeepSeek 最新模型",
            session_id="concurrent-tool",
            round=1,
            retrieval={"rag_enabled": False},
        )

        first, second = await asyncio.gather(
            container.conversation.invoke(request, "same-concurrent-run"),
            container.conversation.invoke(request, "same-concurrent-run"),
        )

        assert first.assistant_message_id == second.assistant_message_id
        assert model.candidate_calls == 1
        assert model.final_calls == 1
        assert capability.calls == 1
        assert first.tool_execution is not None
        assert {
            "tool",
            "status",
            "call_id",
        }.issubset(first.tool_execution)
        assert len(container.sessions.load_all("concurrent-tool")) == 2
        container.conversation._stream_runs.clear()

        class GraphMustNotRun:
            async def astream(self, *args, **kwargs):
                raise AssertionError("durable replay must not execute graph")
                yield

        container.conversation.graph = GraphMustNotRun()
        restored = await container.conversation.invoke(request, "same-concurrent-run")
        assert restored.assistant_message_id == first.assistant_message_id
        assert model.candidate_calls == 1
        assert model.final_calls == 1
        assert capability.calls == 1
        await container.conversation.aclose()

    asyncio.run(exercise())


def test_provider_final_failure_is_audited_and_recoverable_from_durable_events(tmp_path) -> None:
    async def exercise() -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("provider unavailable", request=request)

        client = httpx.Client(transport=httpx.MockTransport(handler))
        container = build_container(
            _settings(
                tmp_path,
                llm_mode="openai",
                llm_base_url="https://provider.invalid/v1",
                llm_api_key="local-test-key",
            )
        )
        container.conversation.dependencies.llm = OpenAICompatibleLanguageModel(client=client)
        request = ChatRequest(
            message="触发失败链审计",
            session_id="provider-failure",
            round=1,
            retrieval={"rag_enabled": False},
        )

        with pytest.raises(RuntimeError, match="无法连接模型服务"):
            await container.conversation.invoke(request, "provider-failure-run")

        durable_events = container.database.conversation_run_events("provider-failure-run", 0)
        attempt_events = [item for item in durable_events if "event: model.attempt" in item]
        assert len(attempt_events) == 1
        assert all('"status": "transport_error"' in item for item in attempt_events)
        terminal = next(item for item in durable_events if "event: run.error" in item)
        assert '"total_http_attempts": 1' in terminal
        assert '"provider_attempts"' in terminal

        container.conversation._stream_runs.clear()
        replay = "".join([item async for item in container.conversation.resume_stream("provider-failure-run")])
        assert replay.count("event: model.attempt") == 1
        assert "event: run.error" in replay
        await container.conversation.aclose()
        client.close()

    asyncio.run(exercise())


def test_new_service_terminalizes_orphaned_running_run_without_reexecution(tmp_path) -> None:
    async def exercise() -> None:
        settings = _settings(tmp_path)
        first = build_container(settings)
        request = ChatRequest(
            message="崩溃前的请求",
            session_id="orphaned-session",
            round=1,
            retrieval={"rag_enabled": False},
        )
        first.database.create_conversation_run(
            run_id="orphaned-run",
            session_id=request.session_id,
            round_num=request.round,
            request_digest=request.idempotency_digest(),
        )
        first.database.append_conversation_run_event(
            run_id="orphaned-run",
            sequence=1,
            event="model.attempt",
            payload=(
                "id: 1\nevent: model.attempt\ndata: "
                '{"version":"1.0","event":"model.attempt","seq":1,'
                '"run_id":"orphaned-run","session_id":"orphaned-session","round":1,'
                '"data":{"attempt":1,"status":"transport_error"}}\n\n'
            ),
        )
        first.database.checkpoint_conversation_run("orphaned-run", "已经生成的部分", 1)
        first.conversation.close()

        with patch.object(type(first.database), "recover_interrupted_runs", return_value=0):
            restarted = build_container(settings)

        class GraphMustNotRun:
            async def astream(self, *args, **kwargs):
                raise AssertionError("orphan recovery must not execute graph")
                yield

        restarted.conversation.graph = GraphMustNotRun()
        before = restarted.database.conversation_run_events("orphaned-run", 0)
        with pytest.raises(RuntimeError, match="run.interrupted"):
            await restarted.conversation.invoke(request, "orphaned-run")
        first_read = restarted.database.get_conversation_run("orphaned-run")
        first_events = restarted.database.conversation_run_events("orphaned-run", 0)
        with pytest.raises(RuntimeError, match="run.interrupted"):
            await restarted.conversation.invoke(request, "orphaned-run")
        second_read = restarted.database.get_conversation_run("orphaned-run")
        second_events = restarted.database.conversation_run_events("orphaned-run", 0)

        assert first_read is not None
        assert first_read["status"] == "interrupted"
        assert first_read["terminal_event"] == "run.interrupted"
        assert first_read["partial_text"] == "已经生成的部分"
        assert first_read == second_read
        assert first_events == second_events
        assert len(first_events) == len(before) + 2
        assert any("event: model.attempt" in item for item in first_events)
        assert any("event: response.replace" in item and "已经生成的部分" in item for item in first_events)
        assert any("event: run.interrupted" in item for item in first_events)
        await restarted.conversation.aclose()

    asyncio.run(exercise())
