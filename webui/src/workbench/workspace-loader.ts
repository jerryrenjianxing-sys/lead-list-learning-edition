import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./api";

export type WorkspaceResource = "jobs" | "datasets" | "analyses" | "artifacts" | "platforms" | "settings" | "diagnostics";
export type ResourceStatus = { loading: boolean; loaded: boolean; error: string; updatedAt?: number };
export type ResourceStates = Partial<Record<WorkspaceResource, ResourceStatus>>;
type WorkspacePayload = { items?: Record<string, unknown>[]; archived_count?: number; [field: string]: unknown };

export const pageResources: Record<string, WorkspaceResource[]> = {
  overview: ["jobs", "datasets", "analyses", "artifacts"],
  jobs: ["jobs", "platforms"],
  data: ["datasets"],
  analyses: ["analyses", "artifacts"],
  platforms: ["platforms"],
  settings: ["settings", "diagnostics"],
};

// Each section keeps its last successful value; one failed read never blocks its peers.
export function useWorkspaceResources(onValue: (key: WorkspaceResource, value: WorkspacePayload) => void) {
  const [states, setStates] = useState<ResourceStates>({});
  const apply = useRef(onValue);
  apply.current = onValue;
  const alive = useRef(true);
  const pending = useRef(new Map<WorkspaceResource, Promise<void>>());
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);

  const refresh = useCallback(async (keys: WorkspaceResource[]) => {
    await Promise.allSettled(keys.map(key => {
      const current = pending.current.get(key);
      if (current) return current;
      setStates(old => ({ ...old, [key]: { loaded: false, error: "", ...old[key], loading: true } }));
      const request = api<WorkspacePayload>(`/${key}`, "GET", undefined, 15000)
        .then(value => {
          if (!alive.current) return;
          apply.current(key, value);
          setStates(old => ({ ...old, [key]: { loading: false, loaded: true, error: "", updatedAt: Date.now() } }));
        })
        .catch(error => {
          if (!alive.current) return;
          setStates(old => ({ ...old, [key]: { loaded: false, ...old[key], loading: false, error: error instanceof Error ? error.message : String(error) } }));
        })
        .finally(() => pending.current.delete(key));
      pending.current.set(key, request);
      return request;
    }));
  }, []);
  return { states, refresh };
}
