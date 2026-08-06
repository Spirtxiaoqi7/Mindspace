import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import { DrawWorkshop } from "./CharacterExperience";

const fateSlots = Array.from({ length: 12 }, (_, index) => {
  const isGold = index === 7;
  return {
    id: `slot-${index + 1}`,
    index: index + 1,
    title: `命格槽位 ${index + 1}`,
    short_title: `槽${index + 1}`,
    icon: "命",
    description: `第 ${index + 1} 条人物维度`,
  };
});

const generatedOptions = Object.fromEntries(fateSlots.map((slot, index) => {
  const isGold = index === 7;
  return [slot.id, [{
      id: `fate-${index + 1}`,
      rarity: isGold ? "gold" : "blue",
      title: `命格 ${index + 1}`,
      summary: `命格方向 ${index + 1}`,
      ...(isGold ? {
        question: "你喜欢一个会主动守护关系的 AI 吗？",
        yes_direction: "主动守护",
        no_direction: "尊重距离",
      } : {}),
    }]];
}));

const options = {
  core_traits: [],
  flaws: [],
  relationships: ["朋友"],
  gender: ["女", "男", "不指定"],
  fate_system: {
    schema_version: "2.0.0",
    rarities: {
      red: { label: "红色", meaning: "带有代价" },
      blue: { label: "蓝色", meaning: "稳定底色" },
      gold: { label: "金色", meaning: "深度定义" },
    },
    slots: fateSlots,
  },
};

beforeEach(() => {
  vi.restoreAllMocks();
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => new Response(JSON.stringify(
    String(input).endsWith("/api/v1/characters/fate-options")
      ? { schema_version: "2.0.0", options: generatedOptions }
      : options,
  ), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  })));
});

it("requires all twelve fates and an explicit gold answer before continuing", async () => {
  const user = userEvent.setup();
  render(<DrawWorkshop defaultUserName="用户" onBack={vi.fn()} onCommitted={vi.fn()} />);

  await waitFor(() => expect(screen.getByLabelText("AI 名称")).toBeInTheDocument());
  await screen.findByRole("button", { name: "不指定" });
  await user.type(screen.getByLabelText("AI 名称"), "林见月");
  await user.click(screen.getByRole("button", { name: "继续" }));

  expect(await screen.findByRole("heading", { name: "先把关系和你想要的TA说清楚" })).toBeInTheDocument();
  await user.type(screen.getByLabelText("你想要一个怎样的角色"), "TA很依赖我，平时百依百顺，受到冷落时会明显不安。");
  await user.click(screen.getByRole("button", { name: "继续" }));

  expect(await screen.findByRole("heading", { name: "选择这次实时生成的命格" })).toBeInTheDocument();
  await waitFor(() => expect(document.querySelectorAll(".fate-candidates button")).toHaveLength(12));
  for (const button of document.querySelectorAll<HTMLButtonElement>(".fate-candidates button")) {
    await user.click(button);
  }
  expect(document.querySelector(".fate-forge-summary")).toHaveTextContent("12/12 已定");

  await user.click(screen.getByRole("button", { name: "继续" }));
  expect(screen.getByText("请完成全部金色命格的是、否或自定义问命")).toBeInTheDocument();

  await user.click(screen.getByRole("button", { name: "否" }));
  await user.click(screen.getByRole("button", { name: "继续" }));
  expect(await screen.findByRole("heading", { name: "合相定格" })).toBeInTheDocument();
});
