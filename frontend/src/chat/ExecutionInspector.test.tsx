import { render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";

import { ExecutionInspector, ToolCard } from "./ExecutionInspector";
import type { InspectorEvent } from "../types";

it("renders logical calls and provider attempts from durable inspector events", () => {
  const events: InspectorEvent[] = [
    { event: "model.summary", label: "模型调用：逻辑 2 · HTTP 3", timestamp: "2026-08-10T12:00:00Z", state: "done", data: { logical_llm_call_count: 2, total_http_attempts: 3 } },
    { event: "model.attempt:1", label: "Provider HTTP 2 · 429", timestamp: "2026-08-10T12:00:01Z", state: "error", data: { attempt: 2, http_status: 429, elapsed_ms: 321 } },
  ];
  render(<ExecutionInspector open tab="flow" onTab={vi.fn()} onClose={vi.fn()} events={events} retrieval={[]} runId="run-a" />);
  expect(screen.getByText("模型调用：逻辑 2 · HTTP 3")).toBeInTheDocument();
  expect(screen.getByText("Provider HTTP 2 · 429")).toBeInTheDocument();
});

it("renders structured partial failures without object stringification", () => {
  render(<ToolCard tool={{
    call_id: "web-1", tool: "web", level: 3, status: "success", parameter_summary: "查询", elapsed_ms: 12, source_count: 1,
    data: { coverage: "partial", partial_failures: [{ provider: "brave_html", error: "HTTP 429" }, "legacy failure"] },
  }} />);
  expect(screen.getByText("brave_html：HTTP 429；legacy failure")).toBeInTheDocument();
  expect(screen.queryByText("[object Object]")).not.toBeInTheDocument();
});
