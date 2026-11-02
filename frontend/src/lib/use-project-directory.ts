"use client";

import { useCallback, useEffect, useState } from "react";
import { api, ApiError, type IndexStatus, type Repository } from "./api/client";

const PAGE_SIZE = 100;
export type ProjectIndexState = IndexStatus["index_status"] | "UNAVAILABLE";

export function useProjectDirectory() {
  const [projects, setProjects] = useState<Repository[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [revision, setRevision] = useState(0);
  const retry = useCallback(() => {
    setLoading(true);
    setError(null);
    setRevision(value => value + 1);
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    async function load() {
      try {
        const all: Repository[] = [];
        let total: number;
        do {
          const page = await api.repositories(PAGE_SIZE, all.length, controller.signal);
          if (controller.signal.aborted) return;
          all.push(...page.repositories);
          total = page.total;
          if (page.repositories.length === 0 && all.length < total) {
            throw new ApiError("INCOMPLETE_LIST", "The API returned an incomplete project list.");
          }
        } while (all.length < total);
        setProjects(all);
      } catch (cause) {
        if (controller.signal.aborted) return;
        setError(cause instanceof ApiError ? cause.message : "Projects could not be loaded.");
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    }
    void load();
    return () => controller.abort();
  }, [revision]);
  return { projects, loading, error, retry };
}

export function useIndexStatuses(projects: Repository[] | undefined) {
  const projectKey = projects?.map(project => project.repository_id).join("|") ?? "";
  const [state, setState] = useState<{ projectKey: string; statuses: Record<string, ProjectIndexState> }>({ projectKey: "", statuses: {} });
  useEffect(() => {
    if (!projects?.length) return;
    const items = projects;
    const controller = new AbortController();
    const key = items.map(project => project.repository_id).join("|");
    async function load() {
      // Limit concurrent requests for large project directories.
      for (let offset = 0; offset < items.length; offset += 6) {
        const batch = items.slice(offset, offset + 6);
        const results = await Promise.all(batch.map(async project => {
          try {
            const status = await api.indexStatus(project.repository_id, controller.signal);
            return [project.repository_id, status.index_status] as const;
          } catch {
            return [project.repository_id, "UNAVAILABLE"] as const;
          }
        }));
        if (controller.signal.aborted) return;
        setState(current => ({ projectKey: key, statuses: { ...(current.projectKey === key ? current.statuses : {}), ...Object.fromEntries(results) } }));
      }
    }
    void load();
    return () => controller.abort();
  }, [projects]);
  return state.projectKey === projectKey ? state.statuses : {};
}
