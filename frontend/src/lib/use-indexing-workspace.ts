"use client";

import { useCallback, useEffect, useState } from "react";
import { api, ApiError, type IndexJob, type IndexStatus } from "./api/client";

const POLL_INTERVAL_MS = 3000;
const terminal = (status: IndexJob["status"]) => status === "COMPLETED" || status === "FAILED";

export function useIndexingWorkspace(repositoryId: string) {
  const [job, setJob] = useState<IndexJob | null>(null);
  const [coverage, setCoverage] = useState<IndexStatus | null>(null);
  const [fileCount, setFileCount] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [working, setWorking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [revision, setRevision] = useState(0);
  const jobId = job?.job_id;
  const jobStatus = job?.status;

  const refresh = useCallback(() => {
    setLoading(true);
    setError(null);
    setRevision(value => value + 1);
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    async function load() {
      const [latest, status, files] = await Promise.allSettled([
        api.latestIndexJob(repositoryId, controller.signal),
        api.indexStatus(repositoryId, controller.signal),
        api.repositoryFiles(repositoryId, controller.signal),
      ]);
      if (controller.signal.aborted) return;
      if (latest.status === "fulfilled") {
        setJob(latest.value);
      } else if (latest.reason instanceof ApiError && latest.reason.code === "INDEX_JOB_NOT_FOUND") {
        setJob(null);
      } else {
        setError(latest.reason instanceof ApiError ? latest.reason.message : "Indexing job history is unavailable.");
      }
      setCoverage(status.status === "fulfilled" ? status.value : null);
      setFileCount(files.status === "fulfilled" ? files.value.total_files : null);
      setLoading(false);
    }
    void load();
    return () => controller.abort();
  }, [repositoryId, revision]);

  useEffect(() => {
    if (!jobId || !jobStatus || terminal(jobStatus)) return;
    const controller = new AbortController();
    let inFlight = false;
    const interval = window.setInterval(async () => {
      if (inFlight) return;
      inFlight = true;
      try {
        const next = await api.indexJob(repositoryId, jobId, controller.signal);
        if (controller.signal.aborted) return;
        setJob(next);
        setActionError(null);
      } catch (cause) {
        if (!controller.signal.aborted) setActionError(cause instanceof ApiError ? cause.message : "Job status could not be refreshed.");
      } finally {
        inFlight = false;
      }
    }, POLL_INTERVAL_MS);
    return () => { controller.abort(); window.clearInterval(interval); };
  }, [repositoryId, jobId, jobStatus]);

  useEffect(() => {
    if (!jobId || !jobStatus || !terminal(jobStatus)) return;
    const controller = new AbortController();
    api.indexStatus(repositoryId, controller.signal).then(value => {
      if (!controller.signal.aborted) setCoverage(value);
    }).catch(() => {
      if (!controller.signal.aborted) setCoverage(null);
    });
    return () => controller.abort();
  }, [repositoryId, jobId, jobStatus]);

  const start = useCallback(async () => {
    if (working) return;
    setWorking(true);
    setActionError(null);
    try {
      const next = await api.startIndex(repositoryId);
      setJob(next);
    } catch (cause) {
      if (cause instanceof ApiError && cause.code === "INDEX_JOB_ACTIVE") {
        try {
          setJob(await api.latestIndexJob(repositoryId));
        } catch {
          setActionError("An indexing job is active. Refresh its status to continue monitoring.");
        }
      } else {
        setActionError(cause instanceof ApiError ? cause.message : "Indexing could not be started.");
      }
    } finally {
      setWorking(false);
    }
  }, [repositoryId, working]);

  return { job, coverage, fileCount, loading, working, error, actionError, start, refresh };
}
