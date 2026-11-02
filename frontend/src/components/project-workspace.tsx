"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useParams, usePathname } from "next/navigation";
import { ArrowUpRight, CalendarDays, GitBranch, Layers3 } from "lucide-react";
import { BackLink } from "@/components/app-shell";
import { ErrorState, LoadingState } from "@/components/states";
import { api, ApiError, type Repository } from "@/lib/api/client";

const tabs = [
  { suffix: "", label: "Overview" },
  { suffix: "/explore", label: "Code explorer" },
  { suffix: "/dependencies", label: "Dependencies" },
  { suffix: "/ai", label: "AI workspace" },
];

export function ProjectWorkspace({ children }: { children: React.ReactNode }) {
  const params = useParams<{ repositoryId: string }>();
  const pathname = usePathname();
  const id = params.repositoryId;
  const [project, setProject] = useState<Repository | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    api.repository(id, controller.signal).then(value => { setProject(value); setError(null); }).catch((cause: unknown) => {
      if (!controller.signal.aborted) setError(cause instanceof ApiError ? cause.message : "Project metadata could not be loaded.");
    });
    return () => controller.abort();
  }, [id, revision]);
  if (error) return <div className="page-stack page-narrow"><BackLink href="/projects">Back to projects</BackLink><ErrorState message={error} onRetry={() => setRevision(value => value + 1)} /></div>;
  if (!project) return <div className="page-stack page-narrow"><BackLink href="/projects">Back to projects</BackLink><LoadingState label="Opening workspace" /></div>;
  const root = `/projects/${project.repository_id}`;
  const date = new Date(project.registered_at).toLocaleDateString("en-US", { year: "numeric", month: "long", day: "numeric" });
  return <div className="page-stack page-narrow"><BackLink href="/projects">All projects</BackLink><div className="workspace-heading"><div className="workspace-glyph"><Layers3 size={27} /></div><div><div className="eyebrow">PROJECT WORKSPACE</div><h1>{project.name}</h1><div className="workspace-meta"><span><GitBranch size={15} />{project.source_alias}</span><span><CalendarDays size={15} />Added {date}</span></div></div><span className="tag tag-success workspace-status"><span className="tag-dot" /> REGISTERED</span></div>
    <nav className="workspace-tabs" aria-label="Workspace sections">{tabs.map(tab => { const href = `${root}${tab.suffix}`; const active = pathname === href; return <Link key={href} href={href} className={active ? "active" : ""} aria-current={active ? "page" : undefined}>{tab.label}</Link>; })}</nav>
    {children}
    <div className="workspace-id">REPOSITORY ID <code>{project.repository_id}</code><ArrowUpRight size={13} /></div>
  </div>;
}
