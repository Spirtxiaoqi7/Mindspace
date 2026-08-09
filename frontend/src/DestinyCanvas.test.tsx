import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";

import DestinyCanvas, { mergeUploadedAvatar } from "./DestinyCanvas";

const slots = Array.from({ length: 12 }, (_, index) => ({
  id: `slot-${index + 1}`,
  index: index + 1,
  name: `节点${index + 1}`,
  axis: `分类${index + 1}`,
  icon: "命",
  x: 12 + (index % 4) * 24,
  y: 14 + Math.floor(index / 4) * 28,
}));
const archetypes = Array.from({ length: 8 }, (_, index) => ({ id: `p${index + 1}`, label: `角色方向${index + 1}`, summary: `第 ${index + 1} 位聊天对象。` }));

let journey: Record<string, any>;
let cardRequests = 0;
let synthesisRequests = 0;
let commitRequests = 0;
let selectionRequests = 0;

function response(payload: unknown) {
  return new Response(JSON.stringify(payload), { status: 200, headers: { "Content-Type": "application/json" } });
}

beforeEach(() => {
  vi.restoreAllMocks();
  window.localStorage.clear();
  vi.stubGlobal("confirm", vi.fn(() => true));
  cardRequests = 0;
  synthesisRequests = 0;
  commitRequests = 0;
  selectionRequests = 0;
  journey = {
    journey_id: "journey-test", schema_version: "3.0.0", revision: 1, status: "seed_ready",
    seed: {}, archetypes: [], cards_by_slot: {}, selections: {}, final_card: null,
    model_calls: { archetypes: 0, cards: 0, synthesis: 0 },
  };
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.endsWith("/api/v1/destiny/definition")) return response({
      slots,
      interaction_willingness: {
        low: { label: "互动意愿降低", meaning: "降低主动聊天意愿" },
        neutral: { label: "不影响互动意愿", meaning: "不直接改变互动意愿" },
        normal: { label: "常规互动意愿", meaning: "保持稳定互动" },
        high: { label: "互动意愿高亢", meaning: "增加主动互动" },
      },
    });
    if (url.endsWith("/api/v1/destiny/journeys") && init?.method === "POST") {
      journey = { ...journey, seed: JSON.parse(String(init.body)) };
      return response(journey);
    }
    if (url.endsWith("/archetypes")) {
      journey = { ...journey, revision: 2, status: "archetypes_ready", archetypes, model_calls: { ...journey.model_calls, archetypes: 1 } };
      return response(journey);
    }
    if (url.endsWith("/cards") && init?.method === "POST") {
      cardRequests += 1;
      const cards_by_slot = Object.fromEntries(slots.map((slot) => [slot.id, archetypes.map((person, personIndex) => ({
        card_id: `${person.id}:${slot.id}`, source_id: person.id, source_label: person.label, slot_id: slot.id,
        slot_name: slot.axis, label: `${person.label} ${slot.axis}`, summary: `聊天中可见的第 ${personIndex + 1} 种表现。`,
        interaction_willingness: (["low", "neutral", "normal", "high"] as const)[personIndex % 4],
      }))]));
      journey = { ...journey, revision: 3, status: "cards_ready", cards_by_slot, model_calls: { ...journey.model_calls, cards: 1 } };
      return response(journey);
    }
    if (url.includes("/selections/") && init?.method === "PUT") {
      selectionRequests += 1;
      const slotId = url.split("/").at(-1)!;
      const body = JSON.parse(String(init.body));
      const card = journey.cards_by_slot[slotId].find((item: { card_id: string }) => item.card_id === body.card_id);
      const selections = { ...journey.selections, [slotId]: card };
      journey = { ...journey, revision: journey.revision + 1, status: Object.keys(selections).length === 12 ? "selections_ready" : "cards_ready", selections };
      return response(journey);
    }
    if (url.endsWith("/synthesize")) {
      synthesisRequests += 1;
      journey = { ...journey, status: "review_ready", final_card: { spec: "chara_card_v2", spec_version: "2.0", data: { name: "林见月", description: "基础信息", personality: "清醒而细腻", scenario: "长期陪伴", first_mes: "我在。", alternate_greetings: ["你好。", "慢慢说。"], mes_example: "{{user}} 你好\n{{char}} 我在。" } } };
      return response(journey);
    }
    if (url.endsWith("/commit")) {
      commitRequests += 1;
      journey = { ...journey, status: "committed" };
      return response({ success: true, character: { character_id: "character-1", display_name: "林见月" } });
    }
    throw new Error(`Unexpected request: ${init?.method ?? "GET"} ${url}`);
  }));
});

it("runs 8 directions, 96 cards and twelve V7 selections before entering local chat", async () => {
  const user = userEvent.setup();
  const onCommitted = vi.fn(async () => undefined);
  render(<DestinyCanvas defaultUserName="测试用户" onBack={vi.fn()} onCommitted={onCommitted} />);

  await user.type(screen.getByLabelText("AI 名称"), "林见月");
  await user.type(screen.getByLabelText("角色期待"), "希望她温柔但有自己的决定，能一起经历真实的日常。");
  await user.click(screen.getByRole("button", { name: "生成命格图" }));

  await waitFor(() => expect(document.querySelectorAll(".destiny-slip")).toHaveLength(3));
  await user.click(document.querySelector<HTMLElement>(".seed-capsule")!);
  expect(screen.getByLabelText("命格生成进度")).toHaveTextContent("8/8");
  expect(screen.getByLabelText("命格生成进度")).toHaveTextContent("96/96");
  await user.click(screen.getByRole("button", { name: "关闭角色种子" }));
  expect(cardRequests).toBe(1);
  await user.click(screen.getByRole("button", { name: "轮换另外三张命签" }));
  expect(cardRequests).toBe(1);

  for (let index = 0; index < 12; index += 1) {
    const candidate = document.querySelector<HTMLElement>(".destiny-slip");
    expect(candidate).not.toBeNull();
    await user.click(candidate!);
    await user.click(screen.getByRole("button", { name: /落契此签|改契此签/ }));
    await waitFor(() => expect(Object.keys(journey.selections)).toHaveLength(index + 1));
    if (index < 11) {
      await screen.findByLabelText(`分类${index + 2}选择舞台`);
      expect(document.querySelector(".destiny-scene")).toHaveClass("is-navigating");
    }
  }

  expect(await screen.findByRole("dialog", { name: "十二项已定" })).toBeInTheDocument();
  expect(screen.getByLabelText("命格进度 12/12")).toHaveTextContent("命盘已成");
  await user.click(screen.getByRole("button", { name: "生成角色并开始聊天" }));

  await waitFor(() => expect(onCommitted).toHaveBeenCalledWith(expect.objectContaining({ character_id: "character-1" })));
  expect(synthesisRequests).toBe(1);
  expect(commitRequests).toBe(1);
  expect(cardRequests).toBe(1);
  expect(selectionRequests).toBe(12);
  expect(window.localStorage.getItem("mindspace.destiny.v7.active")).toBeNull();
}, 20_000);

it("keeps the persistent uploaded avatar URL while preserving crop adjustments", () => {
  expect(mergeUploadedAvatar(
    { src: "/api/v1/avatar/files/destiny-upload.png", aspect: "2 / 3", scale: 1, x: 0, y: 0 },
    { src: "blob:http://127.0.0.1/preview", aspect: "2 / 3", scale: 1.35, x: 12, y: -8 },
  )).toEqual({
    src: "/api/v1/avatar/files/destiny-upload.png",
    aspect: "2 / 3",
    scale: 1.35,
    x: 12,
    y: -8,
  });
});
