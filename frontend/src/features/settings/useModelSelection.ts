import { useCallback, useState, type Dispatch, type SetStateAction } from "react";

import { request } from "../../shared/api";
import type { ProductSettings } from "../../types";

interface UseModelSelectionOptions {
  notify: (message: string) => void;
  setSettings: Dispatch<SetStateAction<ProductSettings | null>>;
}

export function useModelSelection({ notify, setSettings }: UseModelSelectionOptions) {
  const [availableModels, setAvailableModels] = useState<string[]>([]);
  const [modelsLoading, setModelsLoading] = useState(false);

  const loadAvailableModels = useCallback(async () => {
    if (modelsLoading) return;
    setModelsLoading(true);
    try {
      const result = await request<{ models: string[] }>("/api/v1/models/available");
      setAvailableModels(result.models || []);
    } catch (error) {
      notify((error as Error).message);
    } finally {
      setModelsLoading(false);
    }
  }, [modelsLoading, notify]);

  const chooseModel = useCallback(async (model: string) => {
    try {
      const result = await request<{ settings: ProductSettings }>("/api/v1/settings", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ llm: { model } }),
      });
      setSettings(result.settings);
      notify(`已切换到 ${model}`);
    } catch (error) {
      notify((error as Error).message);
    }
  }, [notify, setSettings]);

  return { availableModels, chooseModel, loadAvailableModels, modelsLoading };
}
