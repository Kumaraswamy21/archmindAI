import Link from "next/link";
import { ArrowRight, GitBranch, Layers3 } from "lucide-react";
import type { Repository } from "@/lib/api/client";
import type { ProjectIndexState } from "@/lib/use-project-directory";

const indexLabels: Record<ProjectIndexState, string> = {
  EMPTY: "Not indexed", PENDING: "Index pending", PARTIAL: "Partially indexed",
  FAILED: "Index failed", READY: "Index ready", UNAVAILABLE: "Index unavailable",
};

export function ProjectCard({ project, indexStatus }: { project: Repository; indexStatus?: ProjectIndexState }) {
  const date = new Date(project.registered_at).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
  return <Link className="project-card" href={`/projects/${project.repository_id}`} aria-label={`Open ${project.name} workspace`} data-index-status={indexStatus ?? "LOADING"}>
    <div className="project-card-top"><div className="project-glyph"><Layers3 size={21} /></div><span className="project-card-date">Added {date}</span></div>
    <div><h3>{project.name}</h3><p><GitBranch size={14} />{project.source_alias}</p><span className="project-card-id" title={project.repository_id}>ID {project.repository_id.slice(0, 8)}</span></div>
    <div className="project-card-bottom"><div className="project-card-status"><span className="tag tag-success"><span className="tag-dot" /> {project.status}</span><span className={`index-label ${indexStatus === "READY" ? "index-ready" : indexStatus === "FAILED" ? "index-failed" : ""}`}>{indexStatus ? indexLabels[indexStatus] : "Checking index"}</span></div><span className="open-project">Open project <ArrowRight size={15} /></span></div>
  </Link>;
}
