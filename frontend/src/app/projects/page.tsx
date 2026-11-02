"use client";

import Link from "next/link";
import { Plus, Search, SlidersHorizontal } from "lucide-react";
import { useMemo, useState } from "react";
import { EmptyState, ErrorState, LoadingState } from "@/components/states";
import { ProjectCard } from "@/components/project-card";
import { useIndexStatuses, useProjectDirectory, type ProjectIndexState } from "@/lib/use-project-directory";

type StatusFilter = "all" | "ready" | "in-progress" | "not-indexed" | "attention";

function matchesStatus(status: ProjectIndexState | undefined, filter: StatusFilter) {
  if (filter === "all") return true;
  if (filter === "ready") return status === "READY";
  if (filter === "in-progress") return status === "PENDING" || status === "PARTIAL";
  if (filter === "not-indexed") return status === "EMPTY";
  return status === "FAILED" || status === "UNAVAILABLE";
}

export default function ProjectsPage() {
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<StatusFilter>("all");
  const { projects, error, loading, retry } = useProjectDirectory();
  const statuses = useIndexStatuses(projects);
  const filtered = useMemo(() => projects.filter(project => {
    const searchable = `${project.name} ${project.source_alias} ${project.repository_id}`.toLocaleLowerCase();
    return searchable.includes(query.trim().toLocaleLowerCase()) && matchesStatus(statuses[project.repository_id], filter);
  }), [projects, statuses, query, filter]);
  const checkingStatuses = filter !== "all" && projects.some(project => !statuses[project.repository_id]);
  return <div className="page-stack page-narrow">
    <div className="page-heading"><div><div className="eyebrow with-rule">PROJECT DIRECTORY</div><h1>Projects</h1><p>Every registered repository, with its current index coverage.</p></div><Link href="/projects/new" className="button button-primary"><Plus size={17} /> Add project</Link></div>
    <div className="toolbar"><label className="search-field"><Search size={18} /><span className="sr-only">Search projects</span><input value={query} onChange={event => setQuery(event.target.value)} placeholder="Search name, alias, or ID..." /></label><label className="filter-field"><SlidersHorizontal size={16} /><span className="sr-only">Filter by indexing status</span><select value={filter} onChange={event => setFilter(event.target.value as StatusFilter)}><option value="all">All index states</option><option value="ready">Ready</option><option value="in-progress">In progress</option><option value="not-indexed">Not indexed</option><option value="attention">Needs attention</option></select></label><span className="toolbar-count">{loading ? "LOADING" : `${filtered.length} OF ${projects.length} PROJECTS`}</span></div>
    {loading ? <LoadingState /> : error ? <ErrorState message={error} onRetry={retry} /> : projects.length === 0 ? <EmptyState /> : filtered.length ? <div className="project-grid">{filtered.map(project => <ProjectCard key={project.repository_id} project={project} indexStatus={statuses[project.repository_id]} />)}</div> : checkingStatuses ? <LoadingState label="Checking index status" /> : <EmptyState title="No matching projects" description="Try another search or indexing filter." action={false} />}
  </div>;
}
