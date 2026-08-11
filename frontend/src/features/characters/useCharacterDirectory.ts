import { useCallback, useState } from "react";

import { request } from "../../shared/api";
import type { CharacterSummary } from "../../types";

export function useCharacterDirectory() {
  const [characters, setCharacters] = useState<CharacterSummary[]>([]);

  const loadCharacters = useCallback(async () => {
    const result = await request<{ items: CharacterSummary[] }>("/api/v1/characters");
    const items = result.items || [];
    setCharacters(items);
    return items;
  }, []);

  return { characters, loadCharacters, setCharacters };
}
