import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import { CharacterLibrary, ModeLobby } from "./characters/CharacterExperience";

const summary = { character_id: "character-1", schema_version: "2.0.0", revision: 1, source: "draw" as const, status: "active" as const, display_name: "林见月", gender: "女" as const, user_alias: "", relationship_label: "长期陪伴", avatar: { src: "/avatar.webp", aspect: "2 / 3" as const, scale: 1, x: 0, y: 0 }, latest_session_id: "session-1", created_at: "2026-08-09T00:00:00Z", updated_at: "2026-08-09T00:00:00Z", last_used_at: "2026-08-09T00:00:00Z" };
const record = { ...summary, card: { spec: "chara_card_v2" as const, spec_version: "2.0" as const, data: { name: "林见月", description: "清醒温和的同行者", personality: "细腻且独立", scenario: "长期陪伴", first_mes: "我在。", mes_example: "{{user}} 你好\n{{char}} 我在。", alternate_greetings: [], tags: ["命格"], creator: "Mindspace", character_version: "1.0", extensions: { mindspace: { gender: "女" as const } } } }, memory: { preferences: [], tasks: [] } };

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => new Response(JSON.stringify(String(input).includes("/characters/character-1") ? record : { character: record }), { status: 200, headers: { "Content-Type": "application/json" } })));
});

it("keeps the destiny entry while presenting V2 as the resulting character format", () => {
  render(<ModeLobby characters={[summary]} userName="用户" onDraw={vi.fn()} onCustom={vi.fn()} onLibrary={vi.fn()} onResume={vi.fn()} />);
  expect(screen.getByRole("button", { name: /命定系统/ })).toBeInTheDocument();
  expect(screen.getByText("V2 角色卡")).toBeInTheDocument();
});

it("edits only the compact V2 character fields in the library", async () => {
  const user = userEvent.setup();
  render(<CharacterLibrary characters={[summary]} onBack={vi.fn()} onRefresh={async () => undefined} onChat={vi.fn()} onNewChat={vi.fn()} onDraw={vi.fn()} />);
  expect(await screen.findByDisplayValue("清醒温和的同行者")).toBeInTheDocument();
  await user.clear(screen.getByDisplayValue("清醒温和的同行者"));
  await user.type(screen.getByLabelText("角色描述"), "新的 V2 描述");
  await user.click(screen.getByRole("button", { name: "保存角色卡" }));
  await waitFor(() => expect(vi.mocked(fetch)).toHaveBeenCalledWith(expect.stringContaining("/api/v1/characters/character-1"), expect.objectContaining({ method: "PUT" })));
});

it("offers both resume and new conversation actions from the library", async () => {
  const user = userEvent.setup();
  const onChat = vi.fn();
  const onNewChat = vi.fn();
  render(<CharacterLibrary characters={[summary]} onBack={vi.fn()} onRefresh={async () => undefined} onChat={onChat} onNewChat={onNewChat} onDraw={vi.fn()} />);
  await screen.findByRole("heading", { name: "林见月" });
  await user.click(screen.getByRole("button", { name: "继续对话" }));
  await user.click(screen.getByRole("button", { name: "新建会话" }));
  expect(onChat).toHaveBeenCalledWith(expect.objectContaining({ character_id: "character-1" }));
  expect(onNewChat).toHaveBeenCalledWith(expect.objectContaining({ character_id: "character-1" }));
});

it("requires two clicks before moving a character out of the library", async () => {
  const user = userEvent.setup();
  render(<CharacterLibrary characters={[summary]} onBack={vi.fn()} onRefresh={async () => undefined} onChat={vi.fn()} onNewChat={vi.fn()} onDraw={vi.fn()} />);
  await screen.findByRole("heading", { name: "林见月" });
  await user.click(screen.getByRole("button", { name: "移出角色库" }));
  expect(screen.getByRole("button", { name: "再次点击确认移出" })).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "再次点击确认移出" }));
  await waitFor(() => expect(vi.mocked(fetch)).toHaveBeenCalledWith(expect.stringContaining("/characters/character-1/archive"), expect.objectContaining({ method: "POST" })));
});

it("selects the first character when the library data arrives asynchronously", async () => {
  const props = { onBack: vi.fn(), onRefresh: async () => undefined, onChat: vi.fn(), onNewChat: vi.fn(), onDraw: vi.fn() };
  const view = render(<CharacterLibrary characters={[]} {...props} />);
  expect(screen.getByText("还没有角色")).toBeInTheDocument();
  view.rerender(<CharacterLibrary characters={[summary]} {...props} />);
  expect(await screen.findByRole("heading", { name: "林见月" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "新建会话" })).toBeEnabled();
});
