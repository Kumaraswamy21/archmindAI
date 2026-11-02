"use client";

import { useCallback, useEffect, useState } from "react";
import { api, ApiError, type RepositoryList } from "./api/client";

export function useRepositories(limit = 50, offset = 0) {
  const [data, setData] = useState<RepositoryList | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [revision, setRevision] = useState(0);
  const retry = useCallback(() => setRevision(value => value + 1), []);

  useEffect(() => {
    const controller = new AbortController();
    api.repositories(limit, offset, controller.signal).then(value => {
      if (controller.signal.aborted) return;
      setData(value); setError(null); setLoading(false);
    }).catch((cause: unknown) => {
      if (controller.signal.aborted) return;
      setError(cause instanceof ApiError ? cause.message : "The project list could not be loaded.");
      setLoading(false);
    });
    return () => controller.abort();
  }, [limit, offset, revision]);
  return { data, error, loading, retry };
}
