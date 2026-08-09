"""Evidence-bound, post-turn profile memory writeback."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from mindspace_graph.memory_update import (
    build_memory_extraction_messages,
    parse_memory_plan,
    should_extract_memory,
)
from mindspace_graph.models import ApiConfig, ChatRequest, ChatResponse, JsonWriteReceipt
from mindspace_graph.policies import validate_json_update
from mindspace_graph.ports import Dependencies


class MemoryWritebackService:
    """Extract explicit profile deltas after the visible response is durable."""

    @staticmethod
    def _automatic_patch_allowed(target: str, path: str) -> bool:
        return target == "character_memory" and path in {
            "/preferences/-",
            "/tasks/-",
        }

    def __init__(
        self,
        *,
        dependencies: Dependencies,
        api_provider: Callable[[], ApiConfig],
    ) -> None:
        self.dependencies = dependencies
        self.api_provider = api_provider
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def kick(self, request: ChatRequest, response: ChatResponse) -> None:
        if (
            response.status != "success"
            or request.mode != "primary"
            or request.initiative
            or not should_extract_memory(request.message)
            or not callable(getattr(self.dependencies.llm, "extract_memory", None))
        ):
            return
        key = f"{request.session_id}:{request.round}"
        existing = self._tasks.get(key)
        if existing is not None and not existing.done():
            return
        self._tasks[key] = asyncio.create_task(
            self._run(key, request, response),
            name=f"memory-writeback-{request.session_id[:10]}-{request.round}",
        )

    async def flush_session(self, session_id: str) -> None:
        tasks = [task for key, task in self._tasks.items() if key.startswith(f"{session_id}:") and not task.done()]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def drain(self) -> None:
        tasks = [task for task in self._tasks.values() if not task.done()]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def close(self) -> None:
        for task in self._tasks.values():
            if not task.done():
                task.cancel()

    async def _run(
        self,
        key: str,
        request: ChatRequest,
        response: ChatResponse,
    ) -> None:
        lock = self._locks.setdefault(request.session_id, asyncio.Lock())
        try:
            async with lock:
                profiles = self.dependencies.profiles.load_bundle(request.character_id)
                raw = await asyncio.to_thread(
                    self.dependencies.llm.extract_memory,
                    build_memory_extraction_messages(request, profiles, response.reply),
                    self.api_provider(),
                    timeout_seconds=8.0,
                )
                plan = parse_memory_plan(raw).model_copy(
                    update={
                        "turn_id": f"round_{request.round}",
                        "base_revisions": profiles.revisions,
                    }
                )
                allowed_patches = [
                    patch for patch in plan.patches if self._automatic_patch_allowed(patch.target, patch.path)
                ]
                plan = plan.model_copy(
                    update={
                        "trigger": plan.trigger if allowed_patches else "none",
                        "patches": allowed_patches,
                    }
                )
                validation = validate_json_update(
                    plan,
                    profiles,
                    current_user=request.message,
                    current_response=response.reply,
                )
                if not validation.is_valid or validation.normalized_plan is None:
                    self.dependencies.audit.record(
                        "memory_writeback_rejected",
                        {
                            "session_id": request.session_id,
                            "round": request.round,
                            "errors": validation.errors[:8],
                        },
                    )
                    return
                if not validation.normalized_plan.patches:
                    self.dependencies.audit.record(
                        "memory_writeback_skipped",
                        {
                            "session_id": request.session_id,
                            "round": request.round,
                            "reason": "no_supported_delta",
                        },
                    )
                    return
                receipt = self.dependencies.profiles.apply_json_update(
                    validation.normalized_plan,
                    request=request,
                )
                persisted = self._persisted_ids(request, response)
                receipt = self._bind_evidence(receipt, persisted)
                if self.dependencies.memory is not None and receipt.applied:
                    self.dependencies.memory.record_turn(
                        request,
                        response.reply,
                        persisted=persisted,
                        write_receipt=receipt,
                    )
                if self.dependencies.context is not None and receipt.applied:
                    self.dependencies.context.invalidate(
                        request.session_id,
                        reason="post_turn_memory_writeback",
                        details={"round": request.round, "patch_count": len(receipt.patches)},
                    )
                self.dependencies.audit.record(
                    "memory_writeback_completed",
                    {
                        "session_id": request.session_id,
                        "round": request.round,
                        "patch_count": len(receipt.patches),
                        "targets": sorted({str(item.get("target") or "") for item in receipt.patches}),
                    },
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - the visible turn is already durable
            self.dependencies.audit.record(
                "memory_writeback_failed",
                {
                    "session_id": request.session_id,
                    "round": request.round,
                    "error": str(exc)[:500],
                },
            )
        finally:
            self._tasks.pop(key, None)

    def _persisted_ids(self, request: ChatRequest, response: ChatResponse) -> dict[str, str]:
        user_id = ""
        assistant_id = response.assistant_message_id
        for item in reversed(self.dependencies.sessions.load_all(request.session_id)):
            if int(item.get("round") or 0) != request.round:
                continue
            if item.get("role") == "user" and not user_id:
                user_id = str(item.get("message_id") or item.get("id") or "")
            elif item.get("role") == "assistant" and not assistant_id:
                assistant_id = str(item.get("message_id") or item.get("id") or "")
        return {"user_message_id": user_id, "assistant_message_id": assistant_id}

    @staticmethod
    def _bind_evidence(receipt: JsonWriteReceipt, persisted: dict[str, str]) -> JsonWriteReceipt:
        mapping = {
            "current_user": persisted.get("user_message_id", ""),
            "current_response": persisted.get("assistant_message_id", ""),
        }
        patches: list[dict[str, Any]] = []
        for item in receipt.patches:
            patch = dict(item)
            patch["evidence_ids"] = [
                mapping.get(str(identifier), str(identifier))
                for identifier in item.get("evidence_ids", [])
                if mapping.get(str(identifier), str(identifier))
            ]
            patches.append(patch)
        return receipt.model_copy(update={"patches": patches})
