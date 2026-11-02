import { ArrowRight, Braces, Network, SearchCode } from "lucide-react";
import Link from "next/link";
import { IndexingWorkspace } from "@/components/indexing-workspace";

export default async function WorkspacePage({ params }: { params: Promise<{ repositoryId: string }> }) {
  const { repositoryId } = await params;
  return <><IndexingWorkspace key={repositoryId} repositoryId={repositoryId} /><div className="workspace-intro"><div className="eyebrow with-rule">EXPLORE THE PROJECT</div><h2>Move from status to structure.</h2><p>Use the workspace to inspect source and relationships after the managed index is ready.</p></div><div className="workspace-action-grid"><Link href={`/projects/${repositoryId}/explore`} className="action-card"><span className="action-icon"><SearchCode size={23} /></span><strong>Explore source</strong><p>Navigate the code that makes up this project.</p><ArrowRight size={18} /></Link><Link href={`/projects/${repositoryId}/dependencies`} className="action-card"><span className="action-icon"><Network size={23} /></span><strong>Map dependencies</strong><p>See how modules and symbols connect.</p><ArrowRight size={18} /></Link><div className="action-card action-card-static"><span className="action-icon"><Braces size={23} /></span><strong>Grounded answers</strong><p>Open the AI workspace when the active index reports ready.</p></div></div></>;
}
