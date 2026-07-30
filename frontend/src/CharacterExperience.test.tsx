import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import { DrawWorkshop } from "./CharacterExperience";

const options = {
  core_traits: [
    { id: "gentle", label: "温柔", conflicts: [] },
    { id: "strong", label: "强势", conflicts: [] },
    { id: "rational", label: "理性", conflicts: [] },
  ],
  flaws: [{ id: "stubborn", label: "有些固执", conflicts: [] }],
  relationships: ["朋友"],
  gender: ["女", "男"],
};

beforeEach(() => {
  vi.restoreAllMocks();
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(options), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  })));
});

it("never evicts a selected core trait when a custom trait is added at the limit", async () => {
  const user = userEvent.setup();
  render(<DrawWorkshop defaultUserName="用户" onBack={vi.fn()} onCommitted={vi.fn()} />);

  await waitFor(() => expect(screen.getByLabelText("AI 名称")).toBeInTheDocument());
  await user.type(screen.getByLabelText("AI 名称"), "林见月");
  await user.click(screen.getByRole("button", { name: "继续" }));

  const gentle = await screen.findByRole("button", { name: /温柔/ });
  const strong = screen.getByRole("button", { name: /强势/ });
  await user.click(gentle);
  await user.click(strong);
  expect(gentle).toHaveClass("selected");
  expect(strong).toHaveClass("selected");

  const custom = screen.getByPlaceholderText("自定义核心性格");
  await user.type(custom, "毒舌");
  await user.click(screen.getByRole("button", { name: "加入选择" }));

  expect(gentle).toHaveClass("selected");
  expect(strong).toHaveClass("selected");
  expect(custom).toHaveValue("毒舌");
  expect(screen.getByRole("status")).toHaveTextContent("已经选满 2 项");
  expect(screen.queryByLabelText("已选择的自定义核心性格")).not.toBeInTheDocument();

  await user.click(gentle);
  fireEvent.keyDown(custom, { key: "Enter", code: "Enter" });

  expect(strong).toHaveClass("selected");
  expect(gentle).not.toHaveClass("selected");
  expect(custom).toHaveValue("");
  expect(screen.getByLabelText("已选择的自定义核心性格")).toHaveTextContent("毒舌");
});
