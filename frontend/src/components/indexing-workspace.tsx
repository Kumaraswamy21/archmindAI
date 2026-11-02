"use client";

import Link from "next/link";
import { Activity, ArrowRight, Check, Circle, Clock3, Database, FileCode2, Layers3, LoaderCircle, Play, RefreshCw, Sparkles, TriangleAlert } from "lucide-react";
import { ErrorState, LoadingState } from "@/components/states";
import { useIndexingWorkspace } from "@/lib/use-indexing-workspace";
import type { IndexJob } from "@/lib/api/client";

const stages = [
  { id: "DISCOVERING", label: "Discovering", detail: "Find approved Java source files" },
  { id: "PARSING", label: "Parsing", detail: "Validate syntax and capture source snapshot" },
  { id: "CHUNKING", label: "Chunking", detail: "Split source into structured code chunks" },
  { id: "PERSISTING", label: "Persisting", detail: "Stage chunks in managed PostgreSQL" },
  { id: "EMBEDDING", label: "Embedding", detail: "Generate Gemini vector embeddings" },
  { id: "VECTOR_INDEXING", label: "Vector indexing", detail: "Validate complete vector coverage" },
  { id: "DEPENDENCY_EXTRACTION", label: "Dependency extraction", detail: "Build static relationships" },
  { id: "COMPLETED", label: "Completed", detail: "Promote the verified source snapshot" },
] as const;

function stageState(job: IndexJob | null, stage: (typeof stages)[number]["id"]) {
  if (!job) return "waiting";
  if (job.completed_stages.includes(stage) || (stage === "COMPLETED" && job.status === "COMPLETED")) return "done";
  if (job.current_stage === stage && job.status === "FAILED") return "failed";
  if (job.current_stage === stage && job.status === "RUNNING") return "active";
  if (job.current_stage === stage && job.status === "QUEUED") return "queued";
  return "waiting";
}

const number = (value: number | null | undefined) => value === null || value === undefined ? "—" : value.toLocaleString();

export function IndexingWorkspace({ repositoryId }: { repositoryId: string }) {
  return <IndexingWorkspacePanel key={repositoryId} repositoryId={repositoryId} />;
}

function IndexingWorkspacePanel({ repositoryId }: { repositoryId: string }) {
  const { job, coverage, fileCount, loading, working, error, actionError, start, refresh } = useIndexingWorkspace(repositoryId);
  if (loading) return <LoadingState label="Loading indexing workspace" />;
  if (error) return <ErrorState message={error} onRetry={refresh} />;

  const ready = coverage?.index_status === "READY";
  const active = job?.status === "QUEUED" || job?.status === "RUNNING";
  const jobLabel = job?.status === "QUEUED" ? "Queued for worker" : job?.status === "RUNNING" ? "Indexing in progress" : job?.status === "FAILED" ? "Indexing failed" : job?.status === "COMPLETED" ? "Indexing completed" : ready ? "Index ready" : "No managed indexing job";
  const indexLabel = coverage ? ({ EMPTY: "Not indexed", PENDING: "Pending", PARTIAL: "Partially ready", FAILED: "Failed", READY: "Ready" } as const)[coverage.index_status] : "Unavailable";

  return <div className="indexing-workspace">
    <section className="indexing-summary"><div><span className="eyebrow with-rule">SOURCE SNAPSHOT</span><h2>Indexing overview</h2><p>Track the persisted pipeline from source discovery to a verified index. Counts describe the active snapshot.</p></div><button className="button button-secondary" type="button" onClick={refresh}><RefreshCw size={16} /> Refresh</button></section>
    <section className="index-metrics" aria-label="Project indexing metrics">
      <div className="index-metric"><FileCode2 size={18} /><span>Source files</span><strong>{number(fileCount)}</strong><small>Current approved checkout</small></div>
      <div className="index-metric"><Layers3 size={18} /><span>Code chunks</span><strong>{number(coverage?.total_chunks)}</strong><small>Active snapshot</small></div>
      <div className="index-metric"><Database size={18} /><span>Embeddings</span><strong>{coverage ? `${number(coverage.embedded_chunks)} / ${number(coverage.total_chunks)}` : "—"}</strong><small>{coverage ? indexLabel : "Status unavailable"}</small></div>
      <div className="index-metric"><Activity size={18} /><span>Index status</span><strong className="index-metric-word">{indexLabel}</strong><small>Vector coverage</small></div>
    </section>
    <div className="indexing-columns"><section className="pipeline-card"><div className="pipeline-header"><div><span className="eyebrow">MANAGED PIPELINE</span><h2>{jobLabel}</h2></div><span className={`job-pill job-${(job?.status ?? "idle").toLowerCase()}`}>{job?.status ?? "IDLE"}</span></div>
      {job?.status === "QUEUED" && <p className="pipeline-message"><Clock3 size={16} /> Waiting for the separate indexing worker to claim this job.</p>}
      {job?.status === "FAILED" && <div className="pipeline-failure" role="alert"><TriangleAlert size={18} /><div><strong>{job.error_message ?? "Indexing could not complete."}</strong>{job.error_code && <small>Code: {job.error_code}</small>}</div></div>}
      {actionError && <p className="form-error" role="alert">{actionError}</p>}
      <ol className="stage-list" aria-label="Indexing stages">{stages.map(stage => { const state = stageState(job, stage.id); return <li key={stage.id} className={`stage-item stage-${state}`} aria-current={state === "active" ? "step" : undefined}><span className="stage-marker">{state === "done" ? <Check size={14} /> : state === "active" ? <LoaderCircle size={14} className="spin" /> : state === "failed" ? <TriangleAlert size={14} /> : <Circle size={10} />}</span><div><strong>{stage.label}</strong><small>{stage.detail}</small></div><span className="stage-state">{state === "done" ? "DONE" : state === "active" ? "RUNNING" : state === "queued" ? "QUEUED" : state === "failed" ? "FAILED" : "WAITING"}</span></li>; })}</ol>
      <div className="pipeline-footer"><span>{job ? `Job ${job.job_id.slice(0, 8)} · ${job.started_at ? `Started ${new Date(job.started_at).toLocaleString()}` : "Not started"}` : "No indexing job yet"}</span>{active && <span className="polling-label"><span className="status-light" /> Checking every 3 seconds</span>}</div>
    </section><aside className="index-actions"><span className="eyebrow">NEXT ACTION</span><h2>{ready ? "Your code is ready." : active ? "Indexing is underway." : job?.status === "FAILED" ? "Try indexing again." : "Build a source index."}</h2><p>{ready ? "The active source snapshot has complete vector coverage and can support grounded analysis." : active ? "The worker advances each stage. This page updates automatically and recovers after a refresh." : "A complete index is required before AI analysis can open. A separate worker performs the job."}</p>
      {job?.status === "FAILED" ? <button className="button button-primary index-action-button" type="button" onClick={() => void start()} disabled={working}><RefreshCw size={17} className={working ? "spin" : ""} /> Retry failed indexing</button> : !active && <button className="button button-primary index-action-button" type="button" onClick={() => void start()} disabled={working}><Play size={17} /> {job?.status === "COMPLETED" ? "Reindex project" : "Start indexing"}</button>}
      {ready ? <Link className="button button-secondary index-action-button" href={`/projects/${repositoryId}/ai`}><Sparkles size={17} /> Open AI workspace <ArrowRight size={16} /></Link> : <button className="button button-secondary index-action-button" type="button" disabled aria-describedby="ai-gate-note"><Sparkles size={17} /> Open AI workspace <ArrowRight size={16} /></button>}
      {!ready && <small id="ai-gate-note" className="index-gate-note">Available when the backend reports a ready index.</small>}
    </aside></div>
  </div>;
}
