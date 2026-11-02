"use client";

import Link from "next/link";
import { ArrowRight, ArrowUpRight, Boxes, CircuitBoard, FolderGit2, Plus, ScanSearch } from "lucide-react";
import { EmptyState, ErrorState, LoadingState } from "@/components/states";
import { ProjectCard } from "@/components/project-card";
import { useRepositories } from "@/lib/use-repositories";
import { useIndexStatuses } from "@/lib/use-project-directory";

export default function DashboardPage() {
  const { data, error, loading, retry } = useRepositories(3);
  const indexStatuses = useIndexStatuses(data?.repositories);
  return <div className="page-stack">
    <section className="hero-section">
      <div className="hero-content"><div className="eyebrow with-rule">YOUR CODEBASE, MAPPED</div><h1>Understand the system<br /><em>behind the source.</em></h1><p>Bring approved repositories into one workspace. Trace structure, inspect evidence, and keep every answer anchored to code.</p><div className="hero-actions"><Link href="/projects" className="button button-primary">Explore projects <ArrowRight size={17} /></Link><Link href="/projects/new" className="button button-ghost"><Plus size={17} /> Add a project</Link></div></div>
      <div className="hero-art" aria-hidden="true"><div className="orbit orbit-one" /><div className="orbit orbit-two" /><div className="orbit orbit-three" /><div className="orbit-core"><CircuitBoard size={38} strokeWidth={1.2} /></div><span className="orbit-node n1" /><span className="orbit-node n2" /><span className="orbit-node n3" /><span className="orbit-node n4" /><span className="art-caption">ARCHITECTURE, IN CONTEXT</span></div>
    </section>
    <section className="metrics-grid" aria-label="Workspace summary"><div className="metric"><span className="metric-label">REGISTERED PROJECTS</span><strong>{data?.total ?? "—"}</strong><span className="metric-foot">Available in this workspace</span></div><div className="metric"><span className="metric-label">SOURCE ACCESS</span><strong className="metric-word">Read only</strong><span className="metric-foot">Approved aliases only</span></div><div className="metric"><span className="metric-label">ANALYSIS MODE</span><strong className="metric-word">Evidence first</strong><span className="metric-foot">Answers tied to source lines</span></div></section>
    <section className="section-block"><div className="section-heading"><div><div className="eyebrow">CONTINUE WORKING</div><h2>Your projects</h2><p>Open a repository workspace to inspect its architecture.</p></div><Link href="/projects" className="text-link">View all projects <ArrowUpRight size={16} /></Link></div>
      {loading ? <LoadingState /> : error ? <ErrorState message={error} onRetry={retry} /> : data && data.repositories.length ? <div className="project-grid">{data.repositories.map(project => <ProjectCard key={project.repository_id} project={project} indexStatus={indexStatuses[project.repository_id]} />)}</div> : <EmptyState />}
    </section>
    <section className="capabilities-strip"><div><div className="capability-icon"><FolderGit2 size={19} /></div><strong>Approved sources</strong><span>Projects originate from configured repository aliases.</span></div><div><div className="capability-icon"><Boxes size={19} /></div><strong>Structured analysis</strong><span>Parse, index, and map dependencies with a managed workflow.</span></div><div><div className="capability-icon"><ScanSearch size={19} /></div><strong>Traceable answers</strong><span>Follow citations back to precise source locations.</span></div></section>
  </div>;
}
