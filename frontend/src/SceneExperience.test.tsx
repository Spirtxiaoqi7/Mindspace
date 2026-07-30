import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ScenePickerPage } from "./SceneExperience";
import type {
  CharacterSummary,
  ConversationScene,
  SceneDefinition,
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

const scenes: SceneDefinition[] = [
  {
    scene_id: "riverside_evening",
    title: "河畔夜景",
    description: "沿着河岸散步。",
    location: "河畔夜景",
    asset_id: "scene-riverside",
  },
  {
    scene_id: "rain_window",
    title: "雨夜窗边",
    description: "在窗边听雨。",
    location: "雨夜窗边",
    asset_id: "scene-rainy-room",
  },
];

afterEach(() => {
  vi.unstubAllGlobals();
});

it("switches the conversation background directly without starting an activity", async () => {
  const current: ConversationScene = {
    session_id: "session-a",
    character_id: character.character_id,
    revision: 0,
    scene: null,
    inherited_from_character: false,
    updated_at: "",
  };
  const updated: ConversationScene = {
    ...current,
    revision: 1,
    scene: scenes[1],
    updated_at: "2026-07-30T00:00:00Z",
  };
  const onChanged = vi.fn();
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const url = String(input);
    if (url === "/api/v1/scenes") {
      return new Response(JSON.stringify({ items: scenes, count: scenes.length }), { status: 200 });
    }
    if (url === "/api/v1/sessions/session-a/scene" && init.method === "PUT") {
      return new Response(JSON.stringify(updated), { status: 200 });
    }
    return new Response("{}", { status: 404 });
  });
  vi.stubGlobal("fetch", fetchMock);

  const { container } = render(
    <ScenePickerPage
      character={character}
      sessionId="session-a"
      current={current}
      onBack={() => undefined}
      onChanged={onChanged}
      notify={() => undefined}
    />,
  );

  fireEvent.click(await screen.findByRole("button", { name: "切换到雨夜窗边" }));
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
    "/api/v1/sessions/session-a/scene",
    expect.objectContaining({
      method: "PUT",
      body: JSON.stringify({ scene_id: "rain_window", expected_revision: 0 }),
    }),
  ));
  expect(onChanged).toHaveBeenCalledWith(updated);
  expect(container.querySelector(".scene-experience")).toHaveStyle({
    backgroundImage: 'url("/assets/archive/scenes/scene-rainy-room.webp")',
  });
  expect(fetchMock.mock.calls.some(([url]) => String(url).includes("/activities"))).toBe(false);
});
