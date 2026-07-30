import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { JournalPage } from "./SharedChapters";
import type {
  CharacterSummary,
  JournalEntry,
} from "./types";

const character: CharacterSummary = {
  character_id: "character-a",
  schema_version: "1.0.0",
  revision: 1,
  source: "custom",
  status: "active",
  display_name: "镜",
  gender: "女",
  user_alias: "你",
  relationship_label: "伴侣",
  avatar: { src: "", aspect: "2 / 3", scale: 1, x: 0, y: 0 },
  created_at: "2026-07-30T00:00:00Z",
  updated_at: "2026-07-30T00:00:00Z",
  last_used_at: "2026-07-30T00:00:00Z",
};

const entry: JournalEntry = {
  entry_id: "journal-a",
  character_id: "character-a",
  revision: 1,
  title: "雨夜小记",
  content: "我们确实聊到了窗外的雨。",
  status: "draft",
  source: "assistant_draft",
  session_id: "session-a",
  activity_session_id: "",
  cover_asset_id: "journal-cover-paper",
  source_round_start: 3,
  source_round_end: 6,
  source_message_count: 8,
  visibility: "narrative_only",
  eligible_for_json_evidence: false,
  created_at: "2026-07-30T00:00:00Z",
  updated_at: "2026-07-30T00:00:00Z",
};

afterEach(() => {
  vi.unstubAllGlobals();
});

it("keeps generated journals editable and labels them as non-profile evidence", async () => {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const url = String(input);
    if (url.endsWith("/journal/generate") && init.method === "POST") {
      return new Response(JSON.stringify({
        entry,
        generation: "llm",
        source_scope: { round_start: 3, round_end: 6, message_count: 8 },
      }), { status: 200 });
    }
    return new Response(JSON.stringify({ items: [entry], count: 1 }), { status: 200 });
  });
  vi.stubGlobal("fetch", fetchMock);
  const notify = vi.fn();
  render(
    <JournalPage
      character={character}
      sessionId="session-a"
      onBack={() => undefined}
      notify={notify}
    />,
  );

  expect(await screen.findByText("雨夜小记")).toBeInTheDocument();
  expect(screen.getByText(/主观叙事，不作为人物档案证据/)).toBeInTheDocument();
  expect(screen.getByText(/当前会话第 3–6 轮/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "让角色写一篇日记" }));
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
    "/api/v1/characters/character-a/journal/generate",
    expect.objectContaining({ method: "POST" }),
  ));
  expect(notify).toHaveBeenCalledWith(
    "角色第一人称日记已生成，依据当前会话第 3–6 轮的 8 条消息，保存前仍可编辑",
  );
});
